| Engine | photos | brand right (of 19 legible) | brand wrong | brand claimed, none legible | main amps right (of 9) | panel rating right (of 5) | wrong amp claims | hazard caught (of 1) | false hazard alarms |
|---|---|---|---|---|---|---|---|---|---|
| claude-haiku-4-5-20251001 | 67 | 8/19 = 42% (23%–64%) | 1 | 3 | 1/9 | 2/5 | 0 | 1/1 | 0 |
| claude-opus-5-5 | 67 | 18/19 = 95% (75%–99%) | 0 | 13 | 8/9 | 5/5 | 0 | 1/1 | 1 |
| gpt-5.4-mini | 67 | 15/19 = 79% (57%–91%) | 3 | 25 | 6/9 | 4/5 | 4 | 1/1 | 2 |
| gpt-5.5 | 67 | 16/19 = 84% (62%–94%) | 3 | 16 | 7/9 | 5/5 | 1 | 1/1 | 3 |
| grok-4.20-0309-non-reasoning | 67 | 16/19 = 84% (62%–94%) | 3 | 32 | 6/9 | 4/5 | 14 | 1/1 | 2 |
| grok-4.7 | 67 | 12/19 = 63% (41%–81%) | 0 | 9 | 6/9 | 5/5 | 0 | 1/1 | 0 |
| doctr | 67 | 3/19 = 16% (6%–38%) | 1 | 0 | 1/9 | 1/5 | 22 | 1/1 | 0 |
| paddle | 10 | 2/19 = 11% (3%–31%) | 1 | 0 | 0/9 | 0/5 | 2 | 1/1 | 0 |
| tesseract_psm11 | 67 | 5/19 = 26% (12%–49%) | 1 | 7 | 0/9 | 1/5 | 4 | 0/1 | 0 |
| tesseract_psm3 | 67 | 5/19 = 26% (12%–49%) | 1 | 0 | 0/9 | 2/5 | 2 | 0/1 | 0 |

- claude-haiku-4-5-20251001: p38:brand=other
- claude-opus-5-5: p20:hazard
- gpt-5.4-mini: p05:hazard p06:hazard p16:brand=square_d p22:main=100 p29:main=47 p35:main=20 p40:brand=square_d p60:brand=square_d p76:main=15
- gpt-5.5: p02:brand=square_d p20:brand=challenger p20:hazard p35:main=100 p40:brand=square_d p48:hazard p78:hazard
- grok-4.20-0309-non-reasoning: p02:brand=siemens p06:hazard p12:rating=125 p20:brand=square_d p20:rating=200 p27:hazard p35:main=10 p39:main=200 p39:rating=200 p55:main=200 p55:rating=200 p56:main=200 p60:brand=siemens p65:rating=200 p68:rating=200 p75:main=200 p75:rating=200 p76:main=15 p78:main=200
- grok-4.7: 
- doctr: p07:main=20 p07:rating=20 p22:main=20 p22:rating=20 p30:main=101 p30:rating=101 p40:main=50 p40:rating=50 p50:main=45 p50:rating=45 p52:main=33 p52:rating=33 p54:main=20 p54:rating=20 p63:main=120 p63:rating=120 p64:main=16 p64:rating=16 p66:main=55 p66:rating=55 p75:brand=ge p75:main=18 p75:rating=18
- paddle: p03:main=16 p03:rating=16 p16:brand=square_d
- tesseract_psm11: p12:brand=ge p29:main=44 p29:rating=44 p66:main=55 p66:rating=55
- tesseract_psm3: p37:brand=eaton p66:main=70 p66:rating=70
