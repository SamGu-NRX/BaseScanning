# Meter table sources

`table_version` 1.2.0. Metres are inches times 0.0254, then half-up to four decimal places, except where the sheet also prints centimetres or millimetres and those are exact at four decimal metres. PDFs stay out of git. Each `pdf_sha256` is the SHA-256 of the bytes downloaded on 2026-09-26.

An entry is `usable_for_scale` only when the document states the cover or glass diameter. A line that says only "diameter" is not that statement.

## Change log

### 1.2.0

- OpenWay Riva CENTRON and Gen5 Riva store page 4 letter G, 6.30 in, as the cover (0.1600 m). Letter A, 6.95 in, is the base. Letter B, 5.27 in, is the overall depth. Letter F is the cover front face and is not stored.
- A3 ALPHA figure 2-6, page 2-19, stores the cover as 0.1620 m from 162 mm, and the flange as `base_od_m` 0.1770 from 177 mm.
- CENTRON letter C runs from the flange's front face to the cover front. Letter E is the overall depth. C stays null.
- I-210+ depth 5.25 in MAX stays on `aclara-i-210-plus`. Brochure pages 7-8 are headed I-210+c. The same depth is on the I-210+c FlexNet row.

### 1.1.0

- Fourteen confirmed families added. Cover diameter stays null on every one of them.
- Stored depths, where the sheet names depth or height: iConA Gen 4 and Stratus 0.1270 m, Stratus IQ and IQ+ 0.1286 m, kV2c FlexNet 0.1524 m, kV2c Gen5 0.2083 m.
- C1SRC added to the existing CENTRON type codes. That sheet repeats the C1S columns and is not a new cover.
- AXe, AXR and RXR split off the FOCUS AX row so a type code is not claimed twice.

### 1.0.0

- First table. Two cover diameters, both Itron CENTRON, from sheet 100188SP-06.
- Aclara I-210+ depth, I-210+c FlexNet depth, I-210 CL320 RD (axes unlabeled, so no stored diameter), Landis+Gyr FOCUS AX family (no body diameter).
- No `assumed` values. No generic-prior row. No ANSI-only base row. No sealing-ring row.

## How a number was read

| model_id | printed | stored | letter or label |
|---|---|---|---|
| `itron-centron-c1s-poly` | 6.29 in | `cover_od_m` 0.1598 | drawing A (F matches) |
| `itron-centron-c1s-poly` | 6.95 in | `base_od_m` 0.1765 | drawing B (G matches) |
| `itron-centron-c1s-poly` | 4.53 in | `depth_overall_m` 0.1151 | drawing E |
| `itron-centron-c1s-glass` | 6.42 in | `cover_od_m` 0.1631 | drawing A (F matches) |
| `itron-centron-c1s-glass` | 6.95 in | `base_od_m` 0.1765 | drawing B (G matches) |
| `itron-centron-c1s-glass` | 4.9 in | `depth_overall_m` 0.1245 | drawing E |
| `aclara-i-210-plus` | 5.25 in MAX | `depth_overall_m` 0.1334 | brochure pages 7-8, both headed I-210+c. 5.25 × 0.0254 = 0.13335, stored 0.1334 |
| `aclara-i-210-plus-c-flexnet` | 13.34 cm (5.25 in) | `depth_overall_m` 0.1334 | sheet label "Depth" |
| `aclara-kv2c-flexnet` | 15.24 cm (6 in) | `depth_overall_m` 0.1524 | sheet label "Depth" |
| `aclara-kv2c-gen5` | 8.2 in height | `depth_overall_m` 0.2083 | "Terminal to top of Cover" |
| `sensus-icona-gen4` | 12.70 cm | `depth_overall_m` 0.1270 | sheet label "Depth" |
| `sensus-stratus` | 12.70 cm | `depth_overall_m` 0.1270 | sheet label "depth" |
| `sensus-stratus-iq` | 12.86 cm | `depth_overall_m` 0.1286 | sheet label "depth" |
| `sensus-stratus-iq-plus` | 12.86 cm | `depth_overall_m` 0.1286 | sheet label "depth" |
| `itron-openway-riva-centron` | 6.30 in | `cover_od_m` 0.1600 | page 4, letter G across the cover |
| `itron-openway-riva-centron` | 6.95 in | `base_od_m` 0.1765 | page 4, letter A, base flange |
| `itron-openway-riva-centron` | 5.27 in | `depth_overall_m` 0.1339 | page 4, letter B, overall depth |
| `itron-gen5-riva` | 6.30 in | `cover_od_m` 0.1600 | page 4, letter G across the cover |
| `itron-gen5-riva` | 6.95 in | `base_od_m` 0.1765 | page 4, letter A, base flange |
| `itron-gen5-riva` | 5.27 in | `depth_overall_m` 0.1339 | page 4, letter B, overall depth |
| `elster-a3-alpha` | 162 mm | `cover_od_m` 0.1620 | figure 2-6, page 2-19, across the cover |
| `elster-a3-alpha` | 177 mm | `base_od_m` 0.1770 | figure 2-6, page 2-19, flange |

Not stored, though printed: CENTRON letters C and D; Riva and Gen5 letters C, D, E and F; I-210+ 6.94 in MAX; I-210+c "Diameter" 17.63 cm; I-210 CL320 "6.95 in DIA x 6.75 in H"; FOCUS "5.5 in"; kV2c "7 in Diameter" and FlexNet "Diameter" 17.63 cm; iConA and Stratus "6.95 in diameter"; REX2 approximate drawing.

## Documents

1. Itron CENTRON C1S sheet 100188SP-06, attachment 3803954, `pdf_sha256` `95bd2946fed80a6abc9d46090d8c9223220a0c45bbbc1c88173e6ff3679f5a64`. https://na.itron.com/o/commerce-media/accounts/-1/attachments/3803954
2. Aclara residential brochure, file mark E-0217, `pdf_sha256` `60ee7ca6fa7576d5612f7dad0cd2b107718642ae24b60f4e4f704320b60b77ee`. https://smartgridawareness.org/wp-content/uploads/2017/11/aclara-i-210-residential-electric-metering.pdf
3. Sensus EDS-10002-05, I-210+c with FlexNet, `pdf_sha256` `56714c3529c7f3ec0e8e73d5d2f294cd99e989d85286a75bce5fe881e492ef2f`. https://cdn2.webdamdb.com/v9_md_0k0fKXrHLeu5.jpg.pdf?v=9
4. Hubbell/Aclara ACL-ACLM-SF-EN-00710, `pdf_sha256` `85febecd5c438ce374c2e322ff09433c7027c116e90d306571a79945eb6a9ad7`. https://hubbellcdn.com/literature/ACL-ACLM-SF-EN-00710.pdf
5. Landis+Gyr manual 011808-06 via NY DPS, `pdf_sha256` `91b275f37008596487dc53e1e6c61d4673bc179f14a2f1d823ba02eea9050f39`. https://documents.dps.ny.gov/public/Common/ViewDoc.aspx?DocRefId={BE3EBED1-4973-440D-A2BD-A28ED46F9FC1}
6. Itron OpenWay Riva CENTRON NAM 101441SP-06, `pdf_sha256` `bbccf2701b88fbc802a98161645386aa8254a54028f4bc59b98505708a907fce`. https://na.itron.com/documents/44647/3802945/101441SP-06+OpenWay+Riva+CENTRON+Meter+NAM_Web.pdf/e201f3fd-fbf2-0c13-2911-e53945e5740a?t=1711025904793&version=1.0
7. Itron Gen5 Riva 101763SP-03, attachment 3804170, `pdf_sha256` `d45b6980f04759346501eecc51fdbe3566b121d72e9e4ab1aae09cd2c9aedfa1`. https://na.itron.com/o/commerce-media/accounts/-1/attachments/3804170
8. Itron C1SRC module sheet, attachment 3803723, `pdf_sha256` `abfaf1e95ae3eadc5498e80f454e4a610c633314cc1d8f68ccbe3b9221a37eb1`. Same cover columns as document 1. https://na.itron.com/o/commerce-media/accounts/-1/attachments/3803723
9. Landis+Gyr FOCUS AXe data sheet 2022-1109, `pdf_sha256` `8769247ffcffd554a936b5dfe9fc3e285c04caf0d67698b6c232a9322a02240b`. https://www.landisgyr.com/content/dam/landisgyr/products/apac-product-sheets/2022-1109-LG-Axe351-Data-Sheet.pdf
10. Measurement Canada AE-1641 Rev. 28, `pdf_sha256` `c3139a3893f5ad87b114a34d3397a03fa523c58039ae4153688f572e1c767241`. https://www.hydroquebec.com/data/loi-sur-acces/pdf/c-6186-document-4.pdf
11. NY DPS Revelo E360 filing, `pdf_sha256` `ceae9612e4c671b9bb92904a1e7f9003ad011a454c273fbd663d05368f780204`. https://documents.dps.ny.gov/public/Common/ViewDoc.aspx?DocRefId={A0BE838C-0000-C23C-A366-4593BB4AB878}
12. Sensus EDS-10001-05 iConA Gen 4, `pdf_sha256` `fefe623cb9c6f7ed28f79eb0875631d153e03e52f777f2e3f7e52bfd78e5ea9b`. https://cdn2.webdamdb.com/v5_md_1rJSp6Hj30rd.jpg.pdf?v=5
13. Sensus EDS-10003-04 Stratus, `pdf_sha256` `80985ce43da380e8f03ffe0d325e5a5a0ebfa430ae33be8dc68cf0434957b6ec`. https://cdn2.webdamdb.com/v5_md_5ViNgoZNa1li.jpg.pdf?v=5
14. Sensus EDS-10009-04 Stratus IQ, `pdf_sha256` `f1eea7ab4a5b24537795da99fd8d66accca93127a7c7d06fce89659bebaabae0`. https://cdn2.webdamdb.com/v4_md_QGeBOZCyWrM0.jpg.pdf?v=4
15. Sensus EDS-10012-03 Stratus IQ+, `pdf_sha256` `743e808db8681ee27f6ed69ccdfb8e602cdbd7ad0c5179aabae535059b0807cb`. https://cdn2.webdamdb.com/v3_md_k7H7EwkUMiJ02Ma9.jpg.pdf?v=3
16. Aclara kV2c Gen5 ACL-ACLM-SPEC-EN-02209, `pdf_sha256` `e3d143b35e58aaf82beaa9cece63366d5b73d4e309e97ff8bfe5c6a09a0142e9`. https://hubbellcdn.com/specsheet/ACL-ACLM-SPEC-EN-02209.pdf
17. Aclara kV2c FlexNet FLEXKV2C, `pdf_sha256` `f2facf3f1c69d8acbecf9a20cec1a8c6c9bfbbc3460473a3aa0465aec5ba50ae`. https://cdn2.webdamdb.com/v5_md_21QMxtPJdZL2.jpg.pdf?v=5
18. Honeywell Elster REX2 SEA-DS-NAEN-001017, `pdf_sha256` `1fd005f7046b4d8256e69cfc7589369e680e0a4ccaabb558637f111d64af4fee`. https://itepeyac.com/wp-content/uploads/2025/05/Medidor-Elster-REX2.pdf
19. Elster A3 ALPHA manual TM42-2190B, `pdf_sha256` `34cef0d70402e2da97b2acc0a80840e145aeaede67e679d7fd1ba67c59f77c2d`. https://guggenmossales.com/documents/A3MeterTM42-2190BUSVersion.pdf

## Refused

- Generic prior covers 6.35 / 6.50 / 6.60 in (A9 Monte Carlo). Assumed.
- A standalone ANSI S-base 6.95 in row. The CENTRON base is stored as a datasheet dimension of that meter. The sheet does not quote an ANSI figure for a universal base.
- Retail sealing-ring diameters. The listings were not opened.
- Replacement-cover catalogs that name a fit and do not print a cover diameter.
- Any homeowner meter number, address, or plate photo.
- Manufacturer street addresses printed in sheet footers. They are not in this repo.
