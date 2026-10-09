# Public rules, code citations, prior art and licenses

This was researched on 2026-09-25 from public sources, and Esri's imagery terms were rechecked on 2026-09-26. UNVERIFIED marks a claim nobody could confirm. Licenses change, so recheck one before building a product on it.

## What Base does today

- **Photo checklist.** Base's [help page](https://help.basepowercompany.com/en/articles/10280641) asks for 9 photos. Four cover the meter: a close-up with the number legible, its surroundings from 10 or more steps back, and the areas to its left and right. The others show the adjacent wall corner to corner, the area behind the fence, the main breaker box, the main disconnect with its amperage readable, and the breaker box's surroundings. Base's engineering and installation teams review them and reach out within 2 days if they need more. Nothing public says whether AI is involved.
- **Placement rules.** The [help page](https://help.basepowercompany.com/en/articles/10280705) gives a 3 × 3 ft footprint about 36 in tall. The battery sits within 20 ft of the meter and 1 ft of the wall, and 3 ft from gas meters, fences, AC units and other batteries. It can't stand in front of meters, panels, solar equipment or windows.
- **Meter and panel.** The meter is at most 6 ft up, and the meter and breaker box share a wall. The box can't be in a closet. Both need 30 × 36 in of working space in front. The main breaker is 100 to 200 A, or 150 to 200 A in Austin, and two batteries or solar need a 200 A panel.
- **Transfer switch.** It is about 13 in wide with 30 in of clearance. Base's spec pages ask for 3 ft of wall for the switch, then 3 × 3 ft per battery.
- **Hardware.** Base sells 25 and 50 kWh ground units and Base Core: 39.2 kWh, 39.5 × 30.68 × 22 in, an 11 kW inverter, and a UL listing ([pv magazine](https://pv-magazine-usa.com/2026/08/04/base-power-launches-39-2-kwh-u-s-made-base-core-home-battery-secures-1-billion-in-new-funding/)). The install adds a wall-mounted transfer switch and a battery disconnect. Nothing public mentions a meter collar. The base is integrated, so no concrete pad is needed.
- **Review.** A Deployment Site Designer job post, removed in November 2025, describes people reviewing customer site photos by hand in CAD. Heavy lifting is split from electrical work so crews can do "20 homes in a day".

## Public rule values

The demo's `server/rules.yaml` (PR #11) uses these values, except the 1 ft wall distance, because the solver places the battery flush against the wall. Base's own values are private and stay out of tracked files.

| Rule | Value | Source |
| --- | --- | --- |
| Cable run from the meter | within 20 ft | Base's help page |
| Distance from the wall | within 1 ft | Base's help page |
| Gas meter or regulator | 3 ft | Base's help page, Austin Energy §1.9, Texas Gas Service |
| AC units, fences, other batteries | 3 ft | Base's help page |
| Doors and windows entering the home | 3 ft | IRC R328.4 |
| Working space at the meter and panel | 30 in wide, 36 in deep, 6.5 ft headroom | NEC 110.26 |
| Headroom over the battery | 6.5 ft | NEC 110.26, applied to the battery as a demo choice |
| Driveway, pool | no public value | demo placeholders of 5 ft and 10 ft |

## Code citations

| Code | What it says |
| --- | --- |
| **NEC 110.26** ([ICC](https://codes.iccsafe.org/s/ISEP2021P1/national-electrical-code-nec-solar-provisions/ISEP2021P1-NEC-Sec110.26)) | Working space 30 in wide, or the equipment's width, by 36 in deep, with 6.5 ft of headroom and no storage in it. Base's page says "30 in high × 36 in wide", probably a garbled version. |
| **Austin Energy Design Criteria, Dec 12 2023** ([PDF](https://austinenergy.com/-/media/project/websites/austinenergy/contractors/designcriteriamanual.pdf)) | §1.9.2 asks for 30 in wide, 36 in deep and 6 ft 6 in of headroom at meters, a socket center 30 to 72 in above ground, and at least 1 ft from doors and windows. No meter may sit within a 3 ft radius of gas meters, regulators or relief valves. §1.12 keeps that 3 ft radius for generation enclosures. |
| **Texas Gas Service Meter Setting Requirements** ([PDF](https://www.texasgasservice.com/media/tgs/constructionservices/metersettingrequirements_tgs.pdf)) | Electric meters and outlets stay 3 ft from the regulator relief vent and 3 ft from operable doors and windows. |
| **IRC R328, 2021, from NFPA 855** ([ICC](https://codes.iccsafe.org/s/IRC2021P2/part-iii-building-planning-and-construction/IRC2021P2-Pt03-Ch03-SecR328)) | Batteries are UL 9540 listed and sit outdoors or on exterior walls at least 3 ft from doors and windows. Batteries stay 3 ft apart unless UL 9540A testing allows closer. |
| **NEC 706.15** | The battery disconnect is within sight, and within 10 ft or lockable. |
| **NEC 230.85** | An outdoor emergency disconnect. The 2026 NEC moves it into 230.70. |
| ComEd opening rules | UNVERIFIED. Cite Austin Energy or Texas Gas Service instead. |

## Citation check

Every value in `server/rules.yaml` was rechecked against the source it cites on 2026-10-09. The file is identical on `main` and `t3/server` as of that date (compared with `git diff`), so one table covers both branches. This is a check on whether citations are accurate. It is not a determination that any placement, value or installation is safe, code-compliant or approved; the server still reports UNSURE wherever a scan does not settle a check.

Statuses: **verified** means the source says what the citation claims; **partly supported** means it supports the number with different wording, scope or role; **nothing to check** means the row claims no public source. ICC's Digital Codes viewer renders code text only in a browser and answers automated checkers with 403, so NEC and IRC wording was confirmed from public pages that quote the code; each such page is marked *secondary*.

### Values citing public sources

| rules.yaml value | Cited source | Source reachable | What the source says | Edition or date | Status |
| --- | --- | --- | --- | --- | --- |
| `clearances.gas_ft` = 3.0 ft | Base help page; Austin Energy §1.9; Texas Gas Service | All three. Base renders in a browser; the Austin Energy and Texas Gas PDFs download directly and their text extracts cleanly. | Base: "at least 3 feet apart from gas meters". Austin Energy §1.9.2(C)(4): meters shall not sit "within a circle radius of 3 feet of gas meters, regulators, relief valves, and electrical apparatuses". Texas Gas: electric meter or outlet "3' H x 3' V" from the regulator's relief-valve vent. | Base page edited 2026-09-30; Austin Energy manual effective 2023-12-12; Texas Gas sheet revised 01/22 | Base verified. Austin Energy and Texas Gas partly supported: both are meter-siting rules that put electric equipment 3 ft from gas equipment; neither regulates battery placement. Applying them to the battery is a demo choice. |
| `clearances.ac_ft` = 3.0 ft | Base help page | Yes | "Each battery is roughly the size of an AC unit and needs 3 feet of clearance from gas meters, AC units, fences, other batteries, or other obstructions." The sentence sits in the photo-checklist article (10280641), not the placement article (10280705) the gas rule quotes. | Edited 2026-07-29 | Verified on the value. The citation should name which help page. |
| `clearances.battery_ft` = 3.0 ft | Base help page; IRC 2021 R328 | Both. Base yes; ICC 403s to automated checks. | Base: same sentence as above. IRC R328.3.1: "Individual units shall be separated from each other by not less than 3 feet, except where smaller separation distances are documented to be adequate based on large-scale fire testing" (*secondary*, a city ESS handout quoting the code; UL 9540A is the large-scale fire test method that exception refers to). | Base edited 2026-07-29; IRC 2021; SEAC bulletin 2021-11-30 ties the listing rule to UL 9540 | Partly supported. The 3 ft is verbatim; "unless UL 9540A testing allows closer" paraphrases the fire-testing exception rather than quoting it. |
| `clearances.opening_ft` = 3.0 ft | IRC 2021 R328.4 | Loads in a browser; 403 to automated checks | R328.4, item 3: "Outdoors or on the exterior side of exterior walls. Energy storage systems shall be located not less than 3 feet from doors and windows directly entering the dwelling unit." (*Secondary*; ICC's own viewer titles R328.4 "Locations".) | 2021 IRC | Verified. The wording matches the citation, including "directly entering the dwelling". Garage doors are not named in the code text, which the citation already says. |
| `facing.min_ft` = 3.0 ft | Base help page | Yes | "3 feet of clearance from gas meters, AC units, fences, other batteries, or other obstructions" (photo-checklist article 10280641). | Edited 2026-07-29 | Verified on the value. The citation should name which help page. |
| `headroom.min_ft` = 6.5 ft (placeholder) | NEC 110.26(A)(3), applied to the battery as a demo choice | Loads in a browser; 403 to automated checks | "Section 110.26(A)(3) 'Height of Working Space' requires the height of the working space to be at least 6 feet 6 inches" (*secondary*). | NEC; value unchanged across 2017 to 2023 editions | Verified as a citation. The battery application is a demo choice, which the placeholder flag already says. |
| `meter_working_space.width_ft` = 2.5 ft | NEC 110.26(A)(2) | Loads in a browser; 403 to automated checks | The working space is "at least 30 inches in width or the width of the equipment whichever is greater" (*secondary*). | As above | Verified. 2.5 ft = 30 in. Base's own help page says "30 in high x 36 inches wide", which this file already calls a garbled version. |
| `meter_working_space.depth_ft` = 3.0 ft | NEC 110.26(A)(1) | Loads in a browser; 403 to automated checks | Table 110.26(A)(1): for 0 to 150 volts to ground, depth is 900 mm (3 ft) in every condition (*secondary*). | As above | Verified. 3.0 ft = 36 in. |
| `route.max_ft` = 20.0 ft | Base help page | Yes | "Should be installed within 20 feet of the electrical meter." | Edited 2026-09-30 | Verified. |

### Values citing the repo's own estimates

| rules.yaml value | Cited source | Source reachable | What the source says | Edition or date | Status |
| --- | --- | --- | --- | --- | --- |
| `battery.width_ft` = 2.583333 ft | Base Core spec via this file's Hardware bullet, which cites pv magazine | Yes | The article gives "dimensions of 39.5 by 30.68 by 22 inches" (height by width by depth). *Secondary*: press coverage of Base's spec. | pv magazine, 2026-08-04 | Verified. The source string rounds 30.68 in up to 31 in; 31/12 = 2.583333 ft. |
| `battery.depth_ft` = 1.833333 ft | Same | Yes | 22 in, same sentence. | Same | Verified. 22/12 = 1.833333 ft. |
| `battery.height_ft` = 3.291667 ft | Same | Yes | 39.5 in, same sentence. | Same | Verified. 39.5/12 = 3.291667 ft. |
| `errors.tap_ft` = 0.3 ft | docs/00, Conventions | In repo | "0.3 ft for an AR tap", described as "day-1 estimates, untested". | docs/00 on main | Verified. |
| `errors.vlm_ft` = 1.5 ft | docs/00, Conventions | In repo | "1.5 ft for a position from photo detection". | Same | Verified. |
| `errors.mesh_ft` = 0.5 ft | docs/00, Conventions | In repo | "0.5 ft for the LiDAR mesh". | Same | Verified. |
| `errors.plane_ft` = 0.75 ft (placeholder) | None; untested estimate | n/a | server/README repeats "plane 0.75, an untested estimate". | server/README on main | Consistent; no public source claimed. |
| `errors.tape_ft` = 0.05 ft (placeholder) | "Tape measure read to the nearest half inch" | n/a | 0.05 ft is 0.6 in; half an inch is 0.0417 ft. | n/a | Partly supported by its own wording: the value rounds the half-inch resolution up. |
| `errors.wall_ft` = 0.3 ft | docs/00, Conventions | In repo | Walls come from AR taps, estimated at 0.3 ft. | Same | Verified. |
| `errors.meter_ft` = 0.3 ft | docs/00, Conventions | In repo | The meter is an AR tap, same estimate. | Same | Verified. |
| `errors.drift_per_ft` = 0.16 ft/ft | S1 real-data evals (PR #12) | In repo; PR merged | The ADVIO table reports ARKit-versus-ARCore distance disagreement at median/p90 2.8/8.2 in after 3 ft, 8.6/22.3 after 10 ft and 25.7/60.1 after 30 ft, on a 2018 iPhone 6s running ARKit 1.0. The margins in the rules comment (0.3 ft + 0.16 ft/ft = 0.78, 1.9 and 5.1 ft) cover those p90 values. | PR #12 | Partly supported. The numbers exist, but PR #12 calls them "a disagreement between two trackers, not a bound on ARKit's error". Its position error against ADVIO's truth runs p90 18.6 to 132.6 in, two to three times the 0.16 ft/ft allowance, and its scale error reads 5 to 17% short, not "mostly ~7%". |
| `sweep.step_ft` = 0.166667 ft | docs/00, Decisions | In repo | The sweep "slides the battery's footprint along that line in 2 in steps". | Same | Verified. 2/12 = 0.166667 ft. |
| `sweep.wall_join_ft` = 0.6 ft (placeholder) | "Twice the AR tap error estimate" | n/a | 2 x 0.3 = 0.6. | n/a | Consistent with `tap_ft`. |
| `sweep.meter_to_wall_max_ft` = 3.0 ft (placeholder) | "Input sanity bound" | n/a | No external claim. | n/a | Nothing to check. |
| `clearances.drive_ft` = 5.0 ft (placeholder) | "No public value (docs/04, Public rule values)" | In repo | This file's table lists it as a demo placeholder. | Same | Verified as claimed: no public source. |
| `clearances.pool_ft` = 10.0 ft (placeholder) | "No public value (docs/04, Public rule values)" | In repo | Same. | Same | Verified as claimed. |
| `clearances.wall_equipment_ft` = 0.0 ft (placeholder) | "No public value; 0 means only overlap fails" | n/a | No public claim. | n/a | Nothing to check. |
| `route.confident_reach_ft` = 15.0 ft (placeholder) | "No public value" | n/a | No public claim. | n/a | Verified as claimed. |
| `route.height_ft` = 1.0 ft (placeholder) | "Cable run height above ground" | n/a | No public claim. Base's "within 1 foot of the wall" is a different 1 ft and is not cited here. | n/a | Nothing to check. |
| `route.corner_allowance_ft` = 0.5 ft (placeholder) | "Day-1 estimate: a little per corner; untested" | n/a | No public claim. | n/a | Nothing to check. |
| `ground.allowed` = concrete, gravel, lawn, mulch; `ground.drivable` = drive; `ground.source` (placeholder) | `schemas/scene.schema.json` | In repo | The schema's ground-type enum is drive, concrete, gravel, lawn, mulch, deck. The allowed list picks four of six; the drivable list picks drive. | Schema on main | Verified. "Which surfaces are allowed is a demo choice" is accurate. |

### Flagged

1. The AC, fence and other-battery clearance comes from the photo-checklist help article (10280641); the placement article (10280705) carries only the gas-meter 3 ft, the 20 ft meter distance and the 1 ft wall distance. `ac_ft`, `battery_ft` and `facing.min_ft` say just "Base help page". Name the article.
2. The Hardware bullet credits the battery with "an 11 kW inverter" from the pv magazine article, and calls its listing "a UL listing". The article reports the dimensions and says Base "certified [it] under relevant UL and IEEE safety standards"; it does not give an inverter rating or name a specific standard. Trim or re-cite.
3. Austin Energy §1.9.2(C)(4) and the Texas Gas sheet are meter-siting rules: electric meters and outlets stay 3 ft from gas equipment. Neither governs batteries. The gas_ft row works by analogy, the way `headroom.min_ft` already declares it does.
4. IRC R328.3.1's exception is "large-scale fire testing", which the publicly readable copy (a California-edition handout) ties to CFC 1207.1.5 rather than naming UL 9540A. The substance is the same; the wording in `battery_ft` paraphrases.
5. `drift_per_ft`'s citation quotes p90s of tracker disagreement and calls the scale error "mostly ~7%", where PR #12 measured 5 to 17% and found position error against truth at 2 to 3 times the allowance. Tighten the wording so the number and its context match.
6. `tape_ft` = 0.05 ft rounds the stated half-inch resolution (0.0417 ft) up. Use 0.0417 ft, or say the value is rounded.
7. The two ICC viewer links are the right canonical pointers, but their text is not machine-readable and the checker cannot see it. This file's code quotes were confirmed from public secondary copies; the links stay.

All 16 URLs in this file were link-checked on 2026-10-09 with markdown-link-check and browser headers: 11 answered normally and 5 answered 403 (Esri's blog and the four Roboflow Universe pages); a first pass without browser headers also flagged the two ICC viewer pages. Each of the flagged pages loads in a browser or through a crawler, so the 403s are bot blocking, not dead links. The Roboflow dataset sizes match the counts quoted in this file (402, 1.4k and 1.9k images).

## How others do it

- **Tesla Powerwall** runs a guided self-survey on the phone of about 30 minutes, then designs remotely from the uploaded photos.
- **Qmerit**, for EV chargers, pairs photo upload with Panel Insights, built with Schneider Electric, which reads breaker spaces and capacity from one panel photo ([announcement](https://qmerit.com/news/qmerit-deploying-ai-for-faster-safer-more-accurate-estimates-for-home-ev-charging-installations/)). It is the closest precedent for using AI only to recognize things.
- **SolarAPP+**, from NREL, runs instant automated code checks for solar and storage permits. It is the precedent for a rules engine in place of judgment calls.
- **Aurora Solar** has a Site Surveys API in beta ([docs](https://docs.aurorasolar.com/reference/site-surveys)). **Scanifly** uses drone photogrammetry.
- **Heat pumps.** Aira, Mitsubishi Ecodan and Alpha Innotec offer AR placement that is visual only, with no clearance checks. Fraunhofer ISE's Heat Pump PlanAR, reported on 2026-09-23, scans indoor boiler rooms and optimizes placement. It is the nearest analogue, but it works indoors.
- **3D capture products.** Hover builds a measured exterior 3D model from phone photos and has a JSON API ([docs](https://developers.hover.to/reference/measurements-and-deliverables)), with a turnaround likely measured in hours, so it suits a later cross-check. EagleView sells wall, window and door measurements to enterprises. Polycam's API and Matterport's sandbox are closed to us, magicplan works indoors, and Canvas says it drifts outside.

Prior measurements: iPhone 12 Pro Max LiDAR on facades lands within about ±6 to 8 cm of a total station, and iPad Pro LiDAR reaches about 5 m. A YOLOv5 meter-reading study read the counters 97% of the time and serial numbers only 63%.

None of the products above runs the whole loop outdoors, from metric AR capture through a solver that cites a code for each clearance to an AR preview.

## Licenses and terms

**Aerial imagery.** Use StratMap or NAIP.

| Source | Terms |
| --- | --- |
| Google Maps, Street View | ToS §3.2.3(c)(vii) bans using the content to "train, test, validate or fine-tune" ML, and bans tracing building outlines from its imagery. The Solar API's §20.1 allows use "to determine the feasibility of installing energy systems". Whether running inference on it conflicts with §3.2.3(c) is UNVERIFIED. |
| Mapbox | §1.5(ii) bans using it to "train, operate or improve" ML, and operating includes inference. |
| Esri World Imagery | Automated extraction must start from an exported tile package, stay inside ArcGIS, and keep derived results non-commercial ([Esri](https://www.esri.com/arcgis-blog/products/arcgis-living-atlas/imagery/learn-to-use-ai-to-extract-information-from-world-imagery)). |
| TxGIO StratMap orthoimagery ([site](https://geographic.texas.gov/stratmap/index.html)) | Public domain, with 6 in pixels inside Austin and 12 in elsewhere. |
| NAIP, via Planetary Computer | Public domain, at 0.6 m in Pflugerville (2022) and 0.3 m in Naperville (2023). At 0.6 m a 3 to 5 ft driveway buffer is about 2 pixels. |
| Overture buildings and transportation, OSM | ODbL, whose share-alike applies only if you publish a derived database. OSM tags driveways and pools on under 5% of homes. On 2026-09-25 Pflugerville had 14,675 houses, 149 driveways and 102 pools. Round Rock had 9,223 houses, 476 driveways and 170 pools. Austin had 302k buildings, 6,029 driveways and 3,339 pools. |

**Models.** Check a model's weights license separately from its code license.

| Status | Models |
| --- | --- |
| Noncommercial | MapAnything's default checkpoint (use `facebook/map-anything-apache` instead), Depth Anything 3 Giant and Nested (Metric Large, Base and Small are Apache-2.0), VGGT-Ω, UniDepthV2, Mapillary-Vistas Mask2Former weights, Molmo 2 |
| Restricted or unclear | Depth Pro (Apple personal-use grant), SAM 3 (custom license, gated weights), HY-World 2.0 (custom community license), Metric3D v2 (its files disagree) |
| Copyleft | YOLO-World, YOLOE and Ultralytics (GPL or AGPL), pymeshlab (GPL-3) |
| Permissive | SAM 2, Grounding DINO, OWLv2, Qwen3-VL and zxing-cpp (Apache-2.0), Florence-2, supervision, Open3D and Stray Scanner (MIT), MoGe-2 code and the `Ruicheng/moge-2-vitl-normal` weights at revision cb0e8bb, which the worker pins (MIT, from Hugging Face's metadata for that revision, checked 2026-09-26), shapely (BSD-3) |

For recognition boxes, Gemini 2.5 Pro scored 13.3 zero-shot mAP on RF100-VL, against 1.5 for GPT-5.

**Datasets.** The evals use ADVIO (CC BY-NC 4.0), ETH3D (CC BY-NC-SA 4.0) and MARViN (no license stated) only to measure accuracy, and never redistribute them. Get permission before any commercial use. For training data, Roboflow Universe has gas meter sets of [402](https://universe.roboflow.com/proba-vwwtl/gas-meter-recognition) and [1.4k](https://universe.roboflow.com/gas-meter-zbuni/gas-meter-g7kh6) images and electric meter sets ([wattwise](https://universe.roboflow.com/wattwise/electric-meter-wzeeg-zk5fd), [1,943 images](https://universe.roboflow.com/abhinav-kumar-do8z1/utility-meter-reading-dataset-for-automatic-reading-yolo-z0e1h)). Open Images V7 labels windows and doors. No public data exists for window wells, and open photos of US breaker panels are scarce: PR #17 found 7 against the 40 its test needs.
