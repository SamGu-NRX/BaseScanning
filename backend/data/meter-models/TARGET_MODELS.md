# Meter families to measure

Status is `found` only when a public datasheet or regulatory filing was opened and the product name was on it. `not found` means no such document was opened for this pass. `foreign` means the plates in the B2 check were not US-market meters. A family is a row in `meter_models.v1.json` only when a document confirms it. Cover diameters are filled only when the document names that dimension as the cover or the glass.

`usable_for_scale` is true for five rows: both CENTRON covers, OpenWay Riva CENTRON, Gen5 Riva, and A3 ALPHA. Every other found family is in the table with cover diameter missing.

| Family | Status | Table row | What the document gives |
|---|---|---|---|
| Itron CENTRON C1S / C1SD / C1SR / C1SRC | found | `itron-centron-c1s-poly`, `itron-centron-c1s-glass` | Cover OD A 6.29 in poly / 6.42 in glass; base B 6.95 in; overall depth E 4.53 / 4.9 in. C1SRC repeats the same columns |
| Itron OpenWay CENTRON | not found | — | No CVSO dimensioned sheet opened |
| Itron CENTRON II | not found | — | No dimensioned sheet opened |
| Itron OpenWay Riva CENTRON | found | `itron-openway-riva-centron` | Page 4 letter G 6.30 in across the cover; A 6.95 in base; B 5.27 in depth |
| Itron Gen5 Riva | found | `itron-gen5-riva` | Same drawing. Letter G 6.30 in across the cover. C is 4.34 in and is not stored |
| Aclara I-210+ | found | `aclara-i-210-plus` | Depth 5.25 in max, taken from brochure pages 7-8 headed I-210+c. Horizontal 6.94 in max is not labeled cover or base |
| GE/Aclara I-210+c | found | `aclara-i-210-plus-c-flexnet` | FlexNet sheet: diameter 6.94 in, depth 5.25 in. Diameter is not a cover |
| Aclara I-210 CL320 RD | found | `aclara-i-210-cl320-rd` | "6.95 in DIA x 6.75 in H" with no axis named |
| Aclara/GE kV2c | found | `aclara-kv2c-gen5`, `aclara-kv2c-flexnet` | Gen5: 7 in diameter unlabeled, height 8.2 in stored. FlexNet: diameter unlabeled, depth 6 in stored |
| GE I-210 (separate from I-210+) | not found | — | No GE-branded I-210 sheet beyond the Aclara I-210+ and CL320 documents |
| GE I-60-S | not found | — | No dimensioned sheet opened |
| GE/Chung-Hsin I-70-S | foreign | — | Plate family in the model check; not a US-market entry |
| Landis+Gyr FOCUS AX | found | `landis-gyr-focus-ax` | Manual confirms E330 and E350. No body diameter. Low-profile cover printed "5.5 in" without an axis |
| Landis+Gyr FOCUS AXe | found | `landis-gyr-focus-axe` | 2022 platform sheet. No body diameter |
| Landis+Gyr FOCUS AXR / AXR-SD | found | `landis-gyr-focus-axr` | Measurement Canada AE-1641. No diameter |
| Landis+Gyr FOCUS RXR / RXR-SD | found | `landis-gyr-focus-rxr` | Same approval. No diameter |
| Landis+Gyr Revelo E360 | found | `landis-gyr-revelo-e360` | NY filing names the forms. No body drawing in the downloaded file |
| Elster AB1 | not found | — | No dimensioned sheet opened |
| Elster REX R1S | not found | — | No dimensioned sheet opened |
| Elster/Honeywell REX2 | found | `elster-rex2` | Approximate drawing, "do not use for construction". Nothing stored |
| Elster A3 Alpha | found | `elster-a3-alpha` | Figure 2-6, page 2-19: 162 mm across the cover, 177 mm flange |
| Sensus iConA Gen 4 | found | `sensus-icona-gen4` | Diameter unlabeled. Depth 12.70 cm stored |
| Sensus Stratus | found | `sensus-stratus` | Diameter unlabeled. Depth 12.70 cm stored. Form 2SRD |
| Sensus Stratus IQ | found | `sensus-stratus-iq` | Diameter unlabeled. Depth 12.86 cm stored |
| Sensus Stratus IQ+ | found | `sensus-stratus-iq-plus` | Diameter unlabeled. Depth 12.86 cm stored |
| Sangamo K2A | not found | — | No dimensioned sheet opened |
| Westinghouse D5S | not found | — | No dimensioned sheet opened |
| Tatung D4S | foreign | — | Plate family; `us_market` would be false; no sheet opened |
| Tatung TT-001 | foreign | — | Plate family; no sheet opened |

ANSI C12.10 is named on several sheets as a standard the meter meets. None of those sheets prints an ANSI cover diameter, so there is no `standard` row.
