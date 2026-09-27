# Criteria spec: Base Power site-survey (SSR) checks

Status: draft v0.2, 2026-09-26. Reconciled against the original `SSR_Prompts.md`. Owner: criteria worker (Track C). For Hunter to review.

## Changes in v0.2 (reconciled against the original `SSR_Prompts.md`)

v0.1 was built from Hunter's Notion digest. v0.2 checks it line by line against the original prompts (`internal/criteria/SSR_Prompts.md`, 740 lines, now the authoritative source). **The digest's thresholds were all correct.** Nothing numeric changed. The changes are to outcomes, schemas, and some details the digest dropped.

**Corrections that change behaviour**
1. **A P12 `yes` is a reject.** The source says: "`yes` means the problem is present (a reject)". v0.1 treated `yes` as review-level. §1 and the site rule are updated, so P12 now joins C, Main Panel Type and Recall as a prompt that can reject.
2. **P5 has two separate enums.** v0.1 mixed them into one "subtype". `meterPanelRelationship` may be `insufficient_evidence`, or `not_applicable`, or `co_located_exterior`, or `not_back_to_back`, or `back_to_back`. `relationshipSubtype` may be `unknown`, or `non_garage_meter_wall`, or `exterior_service_equipment`, or `same_wall`, or `90_degree_garage_return_wall`. The prompt also returns `mainPanelLocation`, `meterGaragePosition`, `evidenceSufficiency`, a numeric `confidence` (0–1) and `rationale` (§6).
3. **`needs_more_photos` and `missing_views` exist only in Battery Space (C).** v0.1 applied them to every prompt. The `missing_views` bullets are shown to the member, so they must be written as plain-language instructions (§1, §4.3).
4. **C's own reference photo shows a harness ending at a disconnect box** ("you don't need to worry about that distinction"). So the disconnect Base installed at the Houston house is *not* evidence against "C = Texas". That retracts the previous worker's handoff inference (§7.1).

**Confirmed exact names** (v0.1 guessed these). Full list in `criteria.yaml` → `output_schemas`.
- **Feature prompts:** `hasTrueMultipleMainPanels` + `multipleMainPanelLabel`, `hasLargeOrOversizedMeterCan` + `meterCanLabel`, `hasCl320Meter` + `cl320Label`, each with `insufficientEvidence`.
- **Meter Number:** `meter_number` + `clean-decision` (hyphenated).
- **Meter Mount and Meter Type:** `type` in camelCase (`standardMeter`, `oversizedMeter`, `poleMountedMeter`, `meterBank`, …).
- **Meter–Panel Location:** each P12 field is `{verdict, reasoning}`.
- **B:** output is `{justification, decision}`, with review reason strings `Insufficient wall space for hub` / `Hub Conduit Routing`.
- **A:** the source gives no output block.
- **Base photo-type ids:** `electric_meter_close_up`, `electric_meter_surroundings`, `main_breaker_box_wall`, `main_breaker_box_open`, `main_disconnect_switch_photo`.

**Details the digest dropped, now added**
- **A:**
  - The gas list is meter, regulator and shutoff valve; pipes are C-only.
  - It says "a pool", with no kiddie-pool exemption.
  - Trash cans and boxes are movable; HVAC blocks.
  - It states no door or garage rule. v0.1 inferred one from "continuous wall".
- **B:**
  - Headroom also fails for a crawl-space opening.
  - A short walkway or entry steps is not automatically drivable.
  - The Disco may sit directly above the meter.
- **C:**
  - A car in any photo proves its surface is drivable, even dirt or grass.
  - Pool tells (pump, filter, heater, PVC cluster, floaties) → review.
  - Door, shed-door and gate swing: clear the full arc; don't block the entrance.
  - Comms and cable boxes count as movable.
  - The drive clearance applies to the battery only; the meter and harness may be closer.
  - Pass requires the spot's ~3 ft width, ~5 ft depth, ground and full route to be visible.
  - Models under-estimate large distances.
- **Meter Number:** barcode-wrapper example (`DARG272815824` → `272815824`); a gas-meter-only photo → empty string.
- **Meter Type:**
  - The ratio is invalid if `meter_can_y_min` is 0 or `meter_can_y_max` is 1000.
  - A close-up needs a strip of wall above and below the can.
  - A standard/undersized borderline is also unclean.
- **P12:** built-in enclosures, junction boxes and conduit count as building structure and are ignored. Garage cues are bare studs, garage door, concrete floor, water heater/HVAC and a parked car.
- **Recall:** round push-button breakers → review. An old separate disconnect only tips an otherwise-clean call to review.

**Open conflicts: resolved, confirmed, or new**
- **Resolved:**
  - #20: the "models over-read gaps ~2×" note is in the original, in both B and C.
  - #16: drive clearance is measured from the battery edge ("in addition to the battery's ~2 ft depth").
- **Confirmed present in the original:** #12 (bushes), #13 (missing panel brand), #27 (door crossing).
- **New:**
  - #28: P12 reject vs v0.1's site rule.
  - #29: Meter Type prose vs formula.
  - #30: Main Panel Type's closed-enclosure rule.
  - #31: C comms boxes, movable vs "never under".
  - #32: "ground-level window" is undefined, and the Houston install puts a battery under a window.
  - #33: A's rules are underspecified.
  - Details in §7.

## 0. Source and how to read this

- **Source:** the original `internal/criteria/SSR_Prompts.md` (13 prompts, supplied by Hunter). v0.1 used Hunter's Notion digest ([Site Survey Criteria — Quick Reference](https://app.notion.com/p/3e7071360df881329440ff267511e13d)), which is kept at `internal/criteria/ssr-quick-reference-notion.md` for traceability. Where they differ, the original wins.
- **Machine-readable thresholds and output schemas:** `internal/criteria/criteria.yaml` (v0.2).
- All distances are feet unless stated. "~" means the prompt says "about/roughly".
- **Modality** says what kind of evidence can decide a check:
  - **G (geometric):** needs a metric measurement or spatial relation, so a metric 3D scene, AR taps or LiDAR. Photo guesses are unreliable here: prompts B and C themselves say models over-read tight gaps by ~2× (count brick courses at ~8 in), and C adds that models under-estimate large distances.
  - **P (photo):** scene semantics from ordinary views (what something is, its state, its context). No metric measurement.
  - **R (close-up read):** needs a legible close-up (text, breaker markings, fine condition).
  - Combinations (G+P) mean the object must be recognised in photos and then measured in 3D.

## 1. Outcome vocabulary (not uniform across prompts)

| Prompt family | Output values | Notes |
|---|---|---|
| Placement C, Main Panel Type, Main Panel Recall | `pass` / `manual_review` / `reject` | Decision prompts that can reject. |
| Meter–Panel Location (4 fields) | `yes` / `no` / `manual_review` each, as `{verdict, reasoning}` | **Inverted polarity:** `yes` = problem present, which the source calls **a reject**. `notSameWall` never returns `yes`. |
| Placement A and B | `pass` / `manual_review` | **No reject.** Any failure or doubt goes to review. |
| Feature extractors (P1, P2, P3, P4, P5, P10, P11) | value + label + `insufficientEvidence` (P1–P3), `clean-decision` (P4, P10, P11), or `evidenceSufficiency` + `confidence` (P5) | Facts only; P5 says explicitly "do not decide whether to reject". |
| Battery Space (C) only | `needs_more_photos` + `missing_views[]` | Separate from `manual_review`. A visible borderline gap needs a measurement, not more photos. `missing_views` bullets are shown to the member as plain-language instructions. |

**Site outcome is undefined by Base** (open question). Proposal for our scorer, not Base policy:
1. `reject` if any decision prompt rejects (Placement C, Main Panel Type, Main Panel Recall) or any P12 field is `yes`.
2. Else `manual_review` if any decision prompt or P12 field is `manual_review`, any needed feature is unclean or insufficient, or views are missing.
3. Else `pass`.
Feature prompts and P5 inform people and other prompts; they never change the site outcome by themselves.

## 2. Evidence catalogue

| Base checklist photo (public, 9) | Base photo-type id in the prompts | Packet `purpose` | Feeds |
|---|---|---|---|
| Meter close-up | `electric_meter_close_up` | `meter_close` | P4 meter number, P3 CL320, P2/P11 can size (needs both can edges and a strip of wall above and below) |
| Meter surroundings, 10+ steps back | `electric_meter_surroundings` | `meter_surround` | P10 mount, P12 meter access, placement |
| Left and right of the meter | placement "left", "right" | `meter_left`, `meter_right` | placement (candidate spots, openings, gas, drive) |
| Adjacent wall, corner to corner | placement "around the corner" | `adjacent_wall` | placement (wall ends, needed for a C reject) |
| Behind the fence | placement "behind the fence" | `behind_fence` | placement (side yard, gate, pool) |
| Main breaker box (open) | `main_breaker_box_open` | `panel_open`, `panel_close` | P13 recall/condition, P1/P9 mains |
| Main disconnect with amperage | `main_disconnect_switch_photo` | `main_disconnect` | P1/P9 (which breaker is the service main), P13 secondary signal; P2 only if it shows the same enclosure as the meter |
| Breaker-box surroundings | `main_breaker_box_wall` | `panel_surround` | P12 panel access and panelInHouse, P5 relationship |

Placement prompts say the photos "may be missing, are not in any particular order", and C adds that they may be duplicates or mislabelled, so trust what each image shows over what it was requested as.

**3D observation bands placement needs** (derived from thresholds; see §4.4): meter wall to both natural ends; ground out to ~12 ft from the wall (C pool) or ~7–9 ft (A/B pool and drive); facing to ≥ 5 ft; overhead to ≥ 7 ft above the footprint.

## 3. Summary of the 13 prompts

| # | Source prompt | Our id | Manifest group | Question | Output | Modality |
|---|---|---|---|---|---|---|
| 1 | True Multiple Main Panels | `multiple_mains` | panel_count | Two or more independent service mains? | `hasTrueMultipleMainPanels` + `multipleMainPanelLabel` (`unclear_or_insufficient_evidence` or `true_multiple_main_panels` or `single_main_plus_disconnect_or_subpanel`) + `insufficientEvidence` | P+R |
| 2 | Large or Oversized Meter Can | `oversized_can` | meter_enclosure | Is the meter-holding can oversized? | `hasLargeOrOversizedMeterCan` + `meterCanLabel` (`large_or_oversized_meter_can` / `normal_meter_can` / `meter_can_unclear`) + `insufficientEvidence` | P |
| 3 | CL320 Meter | `cl320` | cl320 | Does readable text say CL320? | `hasCl320Meter` + `cl320Label` (`cl320_meter` / `not_cl320_meter` / `cl320_unclear`) + `insufficientEvidence` | R |
| 4 | Meter Number | `meter_number` | meter_identity | Utility meter number, and is the read clean? | `reasoning`, `meter_number`, `clean-decision` | R |
| 5 | Meter–Panel Relationship | `meter_panel_relationship` | meter_panel_relationship | Where is the panel relative to the meter wall? | 7-field strict JSON (§6) | G+P (cross-view) |
| 6 | Battery Disconnect Placement | `placement.A` | placement | Profile A spot | `pass` / `manual_review` (no output block given) | G+P |
| 7 | Battery Disconnect Placement — 3 ft Window Rule | `placement.B` | placement | Profile B spot (ComEd) | `{justification, decision}`: pass / review | G+P |
| 8 | Battery Space | `placement.C` | placement | Profile C spot + recorded fields | 9-field JSON (§4.3): pass / review / reject | G+P |
| 9 | Main Panel Type | `main_panel_type` | panel_count | Decision on mains | `{decision, justification}`: pass / review / reject | P+R |
| 10 | Meter Mount | `meter_mount` | meter_enclosure | `standardMeter` / `poleMountedMeter` / `meterBank` / `unknown` | `reasoning`, `type`, `clean-decision` | P |
| 11 | Meter Type | `meter_type` | meter_enclosure | `standardMeter` / `oversizedMeter` / `undersizedMeter` / `combinedMeter` / `unknown` | four `_y_min/_y_max`, `distance_ratio`, `reasoning`, `type`, `clean-decision` | P (single-view ratio) |
| 12 | Meter–Panel Location | `meter_not_accessible`, `panel_not_accessible`, `panel_in_house`, `not_same_wall` (source keys `meterNotAccessible`, `panelNotAccessible`, `panelInHouse`, `notSameWall`) | equipment_access | Four problem flags | `{verdict, reasoning}` each; `yes` = reject | G+P |
| 13 | Main Panel Recall | `panel_recall` | panel_condition | Replacement or review triggers | `{justification, decision}`: pass / review / reject | R+P |

## 4. Placement (prompts 6, 7, 8)

### 4.1 Common structure

- Search **both directions** along the meter wall and reachable adjacent walls. One visible, workable spot is enough; a blocked area beside the meter does not rule out the house.
- A spot needs: wall backing, footprint and ground, clearances, headroom, openings, then a supported cable route back to the meter.
- **Unseen means unsure.** Generated or inferred geometry never clears an area.
- **Reject (C only)** needs both natural wall ends seen and every reachable candidate clearly failing. Hidden area, a marginal measurement or a possibly-too-long route blocks a reject.

### 4.2 Thresholds by profile

| Requirement | A · Battery Disconnect | B · 3 ft Window Rule (ComEd) | C · Battery Space | Modality |
|---|---|---|---|---|
| Configuration | meter → disconnect → battery | meter → disconnect → battery | meter → battery harness (disconnect excluded) | – |
| Battery W × D × H | 2 × 2 × 3 ft | 2 × 2 × 3 ft | 31 in × 22 in × 3 ft | – |
| Usable wall width | ≥ 2 ft battery + disconnect ~2 ft beside meter | battery + disconnect 2–2.5 ft near meter | ~3 ft+ | G |
| Wall backing | same wall as meter | same or adjacent wall | exterior building wall or shed wall, flush; not island, fence-only, past a corner, or indoors | G+P |
| Reach | within 15 ft of meter, same wall (metric unstated) | ~25 ft of wall run | routed harness ≤ ~16 ft pass; 16–26 ft review; > ~26 ft infeasible | G |
| Gas | 5 ft radius from meter, regulator, shutoff valve | 5 ft, same list | ~3 ft from any part of battery to meter, regulator, shutoff **and its pipe run** (≈ 6 ft contiguous wall) | G+P |
| Drivable surface | 5 ft radius, +1–2 ft if a drive runs around the spot; concrete, gravel, pavers and cars are indicators; connected surface counts | 5 ft, same; a short walkway or entry steps is not automatically drivable | ~3 ft from the battery, in addition to its depth (meter and harness may be closer); +1–2 ft if the drive runs around; a car in any photo proves its surface drivable, even dirt or grass; garage aprons count | G+P |
| Pool | 5 ft from "a pool" (no kiddie exemption stated) | 5 ft from a permanent pool; kiddie pools exempt | ~10 ft; auto-pass only if clearly in a separate area; pool tells (pump, filter, heater, PVC cluster, floaties, noodles, towels) → review; kiddie pools exempt | G+P |
| Facing clearance (wall → fence/hedge/wall) | not stated | not stated | ≥ ~5 ft including battery depth; borderline → review | G |
| Headroom | not stated | ~6–7 ft; raised porch/deck, low eave, soffit, stairway, landing, crawl-space opening fail | ~6–7 ft; eave, soffit, stairway/landing, raised porch/deck, overhang fail | G |
| Ground | not stated | dirt/lawn/gravel/mulch/concrete, mild slope; decks or extreme slope fail | same plus planter bed; steep concrete fails | P (type) + G (slope, no numeric limit) |
| Openings beside battery | spot needs "clearance on the wall" from windows and electrical equipment (no number) | not under or blocking, a few inches clear, **plus** ≥ 3 ft horizontally each side of door, window well, operable ground-level window; two openings need ≥ ~8 ft gap (≤ ~7 ft fails); can't rule out a well or tell operable/upper-floor → review | don't sit under or block operable ground-level windows, wells, doors; a few inches clear; door/shed-door/gate swinging toward the spot needs its full arc clear, swinging away the battery must not sit in the doorway; unsure → review | P (operable, level, well) + G |
| Exempt openings | – | upper-floor and fixed windows | upper-floor and fixed windows | P |
| Windows on cable route | block the route | harness may pass above/below | harness may pass above/below | P+G |
| Door on route | not stated; inferred blocked from "clear, continuous wall" | never, over or below | above door → review; below/across → prohibited | P+G |
| Garage on route | not stated; inferred blocked | prohibited | prohibited (hard break, not the over-door exception) | P+G |
| Driveway crossing | – | – | prohibited | P+G |
| Other route rules | columns and electrical equipment block | over thin pipes/lines OK | 1 in cable on building wall, support every ~3 ft; above/below windows, behind AC, over small pipes OK; no loose ground run or unsupported gap; fence post: 2 in behind or drill ~4.5 in post | G+P |
| Disconnect / conduit | ~2 ft open wall beside meter, same wall; below meter free | 2–2.5 ft near meter (above OK); clear exit below + ~6 in bend room; boxed-in meter → review | excluded | G (6 in is below learned-depth accuracy; needs AR tap or close-up) |
| Pedestal meter | – | allowed if conduit reaches a nearby wall | – | P |
| Walkways | – | – | keep main entrance path clear; only spot on secondary walkway → review | P+G |
| Service access | keep clear of electrical equipment; small movable items (bins, boxes) fine, HVAC blocks | HVAC/permanent electrical equipment blocks its footprint; spot clear of other electrical equipment on the wall; downspouts, PVC pipes, conduit, hose bibs, small utility/comms boxes don't block | may sit beside equipment with one accessible side; not covering fronts; never directly under a wall-mounted electrical/comms box; not directly under a vent | P+G |
| Street-facing front | – | – | only workable spot on street-facing front → review | P (+GPS/map) |
| Meter access (route start) | – | boxed-in → review (`Insufficient wall space for hub` / `Hub Conduit Routing`) | normal standing room; under a low porch/deck/stairs/crawl space or walled off → at best review; clearly inaccessible with no other meter access → reject | G+P |
| Decision | pass only if every check is confident; else review | pass if a spot + disconnect + route all work; else review | pass / review / reject (§4.3) | – |

**Movable vs fixed (B and C):** plants, bushes, planters, mulch, bins, boxes, furniture, grills, hoses, play equipment, kiddie pools, downspouts, pipes, hose bibs and small utility boxes are movable or non-blocking in both. C also lists comms/cable boxes, a single gate and a short fence section (set `requires_member_action` when the member must move something). HVAC, permanent electrical equipment, the gas meter, operable ground-level windows and wells, permanent pools and a whole parallel fence run are fixed.

### 4.3 Profile C decision rules and recorded fields

- **Pass:** reasonably confident (not certain) that at least one reachable spot works. The spot's ~3 ft width, ~5 ft front depth and ground must be visibly shown (not inferred through bushes, a fence, an AC or a grazing angle), with a visible harness route of ≤ ~16 ft that crosses no door or garage, and no review-only condition. Don't defer a clean spot over trivia such as a tight but usable gap, a downspout, a mild slope, or a window near the meter.
- **Manual review:** marginal measurement; footprint, ground or route obscured; pool, pool-tell or window-well doubt; unsure whether a door/gate swing or entrance is fouled; route over a door; only spot street-facing or on a secondary walkway; route > ~16 ft; `front_clearance_ft` around 5; a far spot possibly out of reach.
- **Reject:** needs near-certainty. Every reachable candidate clearly fails, the wall is seen to its natural end both ways, and no plausible unseen space remains. The justification names both endpoints and why the wall fails all the way to each. A spot possibly beyond ~26 ft is rejected only if the nearest possible spot is unambiguously too far.
- **Photo calibration (C, gas and drive):** within ~1 ft is a confident reject for that spot; an uncertain 2–6 ft gap goes to review; a clearly larger gap passes. With metric capture, use the margin rule instead: pass if measured − error > T, fail if measured + error < T, else review (PR #11/#13 convention).
- **`needs_more_photos`** is true only when the deciding space is unseen, or the one spot relied on is obscured. It is false when a clean spot is found or the space was seen and is bad ("seen-and-bad is not a flag; unseen-and-maybe is").
- **Output (exact keys):** `justification`, `reference_object` (with its assumed size), `distance_ft` (routed length; for a reject, the distance to the nearest spot considered), `front_clearance_ft` (empty gap; ~99 = wide open), `drive_path_ft` (from the best photo showing both spot and drive; null if no spot or no drive), `requires_member_action` (false on reject), `decision`, `needs_more_photos`, `missing_views[]` (plain-language member instructions; `[]` when false).

### 4.4 What 3D must deliver for placement

| Band | C | A / B |
|---|---|---|
| Wall | meter wall + adjacent walls to natural ends (reject) or across spot and route (pass) | same wall (A); same + adjacent (B) |
| Ground out from wall | ≥ 11.8 ft (10 ft pool + 1.83 ft depth) to clear a pool; ≥ 4.8–6.8 ft for drive | ≥ 7 ft (5 ft + 2 ft depth), up to 9 ft with drive caution |
| Facing | ≥ 5 ft or the obstruction | not required |
| Overhead | ≥ 7 ft above footprint | ≥ 7 ft (B) |
| Resolution | ±0.5 ft is enough away from thresholds; near-threshold values go to review | B conduit 6 in bend room needs ≤ 2 in accuracy |

Accuracy context (real data, `internal/house-scanning-context.md`): learned depth is ≥ 5 in p90 on walls and ≥ 8 in on edges; a current phone's ARKit drifts 8.6 / 13.4 / 18.5 in p90 at 10 / 20 / 30 ft. So 3–5 ft thresholds are decidable away from the margin; 6 in rules are not.

## 5. Meter prompts

### P4 Meter Number (`meter_number`) · R
- **Evidence:** original close-up of exactly one electric meter, every character legible.
- **Read:** prefer the close-up (surroundings only as fallback), and prefer the utility-facing label number over the manufacturer barcode serial. Exclude kWh readings, CL/class/form, volts/amps/phase, model/FCC/UL ids, service-equipment labels, addresses, account/ESI numbers, breaker ratings, panel labels, handwritten notes. Strip barcode wrappers (`DARG272815824` → `272815824`).
- **`clean-decision` = false:** multiple/duplex meters or a bank; partial meter or label; scratch, sticker, dirt, glare or obstruction; digits too small to resolve individually; no electric meter (or gas meter only).
- **Output:** `reasoning` (1–3 sentences), `meter_number` (best effort, even when not clean; empty string only if no electric meter number is photographed), `clean-decision`. The value is a field-hydration suggestion; an unclean suggestion is never a trusted id.

### P3 CL320 Meter (`cl320`) · R
- **`cl320_meter` only if** readable meter-face or meter-label text explicitly says CL320. CL200 / Class 200 / FORM 2S CL200 is `not_cl320_meter`. Can size, service equipment, breaker amperage or panel labels never substitute unless they say CL320.
- **Unreadable:** `cl320_unclear`. Set `insufficientEvidence` true if neither a meter face nor a service-equipment label can be read.
- Role in the site outcome is not stated (open question).

### P2 Large or Oversized Meter Can (`oversized_can`) · P
- Judge the enclosure directly holding the round meter, not a neighbouring disconnect, load centre, solar or transfer equipment. A CL200 meter can sit in an oversized can, so CL200 text is not counter-evidence. A 225A breaker or 200A disconnect label is not evidence either. Disconnect or panel photos count only if they show the same enclosure as the meter.
- **`large_or_oversized_meter_can`:** visibly much larger than a compact socket, or a large 320A-style can. **`normal_meter_can`:** roughly just the enclosure around the round meter. Size unknown → `meter_can_unclear`.

### P11 Meter Type (`meter_type`) · P
- **`distance_ratio`** divides can height by head height: (`meter_can_y_max` minus `meter_can_y_min`) over (`meter_head_y_max` minus `meter_head_y_min`), from **one photo** in 0–1000 normalised vertical coordinates (head = glass/dome only). Both can edges must be visible (a close-up needs a strip of wall above and below), and the head must be ≥ ~10 units. The ratio is invalid if `meter_can_y_min` = 0 or `meter_can_y_max` = 1000. With no usable view, all four coordinates and the ratio are 0, not clean. It is an image heuristic, not feet, and a supporting signal rather than a hard rule.
- **`standardMeter`** ≈ 2 heads; **`oversizedMeter`** ≈ 3–4 heads (two more heads fit above and below); **`undersizedMeter`** ≈ 1–1.5 heads, usually narrower too; **`combinedMeter`** = meter and main breaker in one unit. A borderline between standard and oversized (~3) or between standard and undersized is not clean. The prose and the formula disagree on whether the head is counted (conflict #29).
- **`clean-decision` = false:** multiple meters; poor visibility; no single photo with both can edges and the head; standard-sized but unlike the examples; not on a can; any doubt. Still give a best-guess `type`; `unknown` only if genuinely indeterminable (no meter).
- 3D could supply a metric can height, but the prompt defines the check as the single-view ratio.

### P10 Meter Mount (`meter_mount`) · P
- **`standardMeter`:** single meter on the home's exterior wall. **`poleMountedMeter`:** standalone pole. **`meterBank`:** clear in-line row (≥ 3 together is the confident signal). **`unknown`:** only when no meter is shown.
- **`clean-decision` = false:** no meter or poorly visible meter; mounting context not shown; distant, extreme-angle, cluttered or partly blocked shot; propped-up meter; exactly two meters, separated meters, or uncertain alignment; any doubt. A clear bank is a clean mount even though it makes P4 and P11 unclean.

## 6. Panel and relationship prompts

### P1 True Multiple Main Panels (`multiple_mains`) · P+R
- **`true_multiple_main_panels`** only with ≥ 2 panels, each with a clearly visible independent main breaker/disconnect (open, close-up or readable-label view) and evidence that each is an independent service main. Wall-context photos alone never prove it.
- Not counted by appearance: adjacent or closed boxes, service disconnects, transfer switches, emergency disconnects, meter-main equipment, solar equipment, sub-panels, a prominent 2-pole breaker alone. If one feeds the other, or only one has a visible service main → `single_main_plus_disconnect_or_subpanel`, false.
- Unknown relationship → `unclear_or_insufficient_evidence`. A closed or distant second enclosure → false or unclear, never inferred true. `insufficientEvidence` = true when panel images are missing, cropped, blurry or closed. "Not proven multiple" ≠ "single proven".

### P9 Main Panel Type (`main_panel_type`) · P+R
- Judges panel count only (not location or condition). A separate disconnect enclosure is usually the single main's disconnect.
- **Reject** only when each of ≥ 2 separate service enclosures shows its own independent main breaker/disconnect. **Pass:** a single main, optionally with sub-panels or disconnects. **Review:** two similar panels without an observable main in each; candidate mains in different locations; unsure whether it's a sub-panel or a main; insufficient photos. A wrong pass and a wrong reject are both costly. See conflict #30 on closed enclosures.

### P5 Meter–Panel Relationship (`meter_panel_relationship`) · G+P
- Facts, not a decision ("do not decide whether to reject"; no business policy). Strict JSON, and each list below is a set:
  - `mainPanelLocation`: `unknown` or `not_visible` or `non_garage_interior` or `exterior_service_equipment` or `garage_interior`
  - `meterGaragePosition`: `unknown` or `not_visible` or `not_garage_wall` or `garage_exterior_wall_other` or `wall_immediately_adjacent_to_garage_opening`
  - `meterPanelRelationship`: `insufficient_evidence` or `not_applicable` or `co_located_exterior` or `not_back_to_back` (being in the "same garage" does not qualify) or `back_to_back` (the panel is on the inside face of the meter's wall)
  - `relationshipSubtype`: `unknown` or `non_garage_meter_wall` or `exterior_service_equipment` or `same_wall` or `90_degree_garage_return_wall`
  - `evidenceSufficiency`: `insufficient` or `sufficient`; `confidence`: 0.0–1.0; `rationale`
- Exterior panel near the meter → `co_located_exterior` (don't force a wall relationship). Panel not visible → `insufficient_evidence`. For a garage panel with the meter beside the garage opening, actively check for a perpendicular return wall.
- Anchors: windows, garage-door edges, corners, interior wall returns, conduit.
- **Geometric by nature:** it asks which wall face the panel sits on. An exterior-only reconstruction cannot settle it. If the iOS capture keeps **one AR session** from the meter to the panel, the relative pose settles it directly; otherwise only visual anchors can.

### P12 Meter–Panel Location · G+P
Working envelope per device: ≥ ~3 ft out from the face × ≥ 2.5 ft wide centred ("don't be overly strict"). Overhead obstruction below ~6 ft → review; gas meter/regulator within ~3 ft → review. Ignore trivially movable items and building structure (a jutting wall, built-in enclosures, junction boxes, conduit). `yes` is a **reject** per the source. Output: each key is `{verdict, reasoning}`.

| Field | `no` (pass) | `manual_review` | `yes` (problem) |
|---|---|---|---|
| `meter_not_accessible` | clear envelope; trivial movable items and building structure ignored | substantial storage, fixed shelf, low headroom, nearby gas, frontage unseen | fixed obstruction in envelope: bush/overgrowth, AC condenser, water heater |
| `panel_not_accessible` | same | same | fixed obstruction, or panel walled/boxed off |
| `panel_in_house` | clearly in the garage (cues include a garage door, a parked car, a concrete floor, exposed studs, or water heater/HVAC) or outside | garage vs living space unclear; cropped/dark | clearly in living space (closet, hallway, room, laundry, finished basement, attic) |
| `not_same_wall` | both plainly outside on the same exterior wall | inside, different walls, or not proven | never |

A shared object across photos does not prove the same wall.

### P13 Main Panel Recall (`panel_recall`) · R+P

| Finding | Result |
|---|---|
| Brand text confirms FPE / Federal Pacific / Stab-Lok, Challenger, Zinsco / GTE-Sylvania / Zinsco-Sylvania / Magnetrip, Sylvania | reject (read text, not appearance) |
| Heavy or widespread corrosion on enclosure, dead-front or breakers | reject |
| Any vertically operating breaker with up = OFF, down = ON | reject (horizontal handles exempt; unreadable → review) |
| Westinghouse; light or uncertain rust; old look (e.g. round push-button "ring" breakers); exposed/loose wiring or missing dead-front; unreadable ON/OFF | review |
| Old or heavily rusted main breaker or separate disconnect | secondary signal: only tips an otherwise-clean call to review |
| Missing or unreadable brand, ambiguous evidence | review (the source also has a looser "no visible recalled brand" pass phrase; unresolved) |
| Modern panel, none of the above, evidence sufficient | pass |

## 7. Reconciliation with the three rule versions and other rule sets

Compared: Base profiles A/B/C (above, now checked against the original prompts), `internal/house-scanning-context.md` (its summary of the same prompts), the repo's public baseline (`docs/01`, `docs/04`), and the PR #11 server `rules.yaml` placeholders. The context doc's A/B/C table matches the original on every number. The conflicts below are between profiles, within the source, or between Base and what the team built. Status tags: **[confirmed]** present in the original; **[resolved]** settled by the original; **[new v0.2]** found while reading the original.

### 7.1 Between profiles A, B and C (need Base's choice)

| # | Topic | A | B | C |
|---|---|---|---|---|
| 1 | Gas | 5 ft | 5 ft | ~3 ft |
| 2 | Drive | 5 ft | 5 ft | ~3 ft beyond footprint |
| 3 | Pool | 5 ft | 5 ft | ~10 ft |
| 4 | Reach and its **unit** | 15 ft from meter, same wall (straight line or route unstated) | ~25 ft of **wall run** | ~26 ft **routed cable**, > 16 ft review |
| 5 | Battery size | 2 × 2 × 3 ft | 2 × 2 × 3 ft | 31 × 22 × 36 in (public Base Core spec: 39.5 in tall; the server uses 39.5) |
| 6 | Openings beside battery | "clear of windows", no number | 3 ft each side (8 ft+ between openings) | a few inches, don't sit under or block |
| 7 | Windows on route | block | allowed above/below | allowed above/below |
| 8 | Over a door | continuous wall needed | prohibited | review |
| 9 | Disconnect | 2 ft beside meter, same wall | 2–2.5 ft near meter + 6 in bend room | none |
| 10 | Reject possible | no | no | yes |
| 11 | Adjacent wall | no | yes | yes |

All eleven rows are **[confirmed]** in the original. B states that the house is on ComEd (Commonwealth Edison, Illinois) and that this utility uses a tighter window clearance, so B applies to ComEd territory. Base operates mainly in Texas, so C (or A) is the likely non-ComEd profile (inference; confirm).

**Real-install evidence (`yt-12Ks1idWj40`, Houston, CenterPoint).** Base installed a white BASE disconnect beside the existing grey enclosure, plus two batteries, one partly under a window. The disconnect does **not** argue against C: C's own reference photo shows a harness ending at a small disconnect box, and C says "you don't need to worry about that distinction". So C treats the disconnect as installed hardware that placement ignores. The battery under the window is the real tension (#32).

### 7.2 Within the source (ambiguities to resolve with Base)

12. **[confirmed] Bushes:** movable in B and C, but in P12 "overgrown vegetation or a bush" in the working envelope is a fixed obstruction (`yes`, a reject).
13. **[confirmed] Missing panel brand:** Recall's review list says "brand label unreadable, missing, or ambiguous → manual_review", but its pass text allows "a legible non-recalled brand (or no visible recalled brand)". We follow review.
14. **[confirmed] Headroom** is a range (~6–7 ft), not a threshold. We use: < 6 ft fail, ≥ 7 ft pass, between → review unless measured with confidence.
15. **[confirmed] Slope** has no number ("mild" vs "extreme/steep").
16. **[resolved] Drive distance origin:** C counts drive clearance from roughly where the battery itself sits, so the battery's ~2 ft depth is included, while the meter and the harness can sit closer than the battery. So it's ≥ 3 ft from the battery's outer edge (≈ 4.8 ft from the wall), as the server measures.
17. **[confirmed] Meter Type** ~3-head borderline: the source settles it as unclean with a best-guess type.
18. **[confirmed] Pole meter:** P10 classifies it; only B addresses eligibility (allowed: place the Disco and the battery on a nearby wall that the conduit can reach). Under C a harness may not run loose over ground, so a pole meter may be infeasible; unstated.
19. **[confirmed] Role of CL320, oversized can and meter number** in eligibility is never stated; the prompts are extractors only.
20. **[resolved] "Models over-read gaps ~2×"** is in the original, in both B and C: for tight between-openings gaps, count ~8 in brick courses. C adds that models under-estimate large distances.
28. **[new v0.2] P12 `yes` is a reject.** The source says "`yes` means the problem is present (a reject)". v0.1's site rule mapped `yes` to review. Fixed in §1. So an AC condenser, water heater, bush or boxing-in in front of the meter or panel rejects the site even when placement passes.
29. **[new v0.2] Meter Type prose vs formula.** The prose says a standard can is "about 2 meter-heads tall … this does not include meter-head itself" and an oversized one 3–4 heads "not including meter-head itself". But `distance_ratio` divides full can height (head included) by head height, with guide values of 2 / 3–4 / 1–1.5. Read literally, the prose puts a standard can at ~3 heads including the head, which collides with the oversized band. We follow the formula; ask Base.
30. **[new v0.2] Main Panel Type, closed enclosures.** One sentence says "a candidate enclosure [that] is closed or too distant … treat it as a single main plus a subpanel/disconnect" (pass-leaning). The review list says "two similar/identical panels, but no observable main breaker … the box is closed → manual_review". P1 says "return false or unclear". We use: two comparable enclosures without visible mains → review; an obviously smaller or different closed box beside one visible main → single main.
31. **[new v0.2] C comms boxes.** "Comms / cable boxes" and "small utility boxes" are movable or non-blocking in C, yet C forbids siting the battery immediately beneath a wall-mounted electrical box or a comms box. We use: a wall-mounted comms box above the spot blocks it; a loose or ground-standing box doesn't.
32. **[new v0.2] "Ground-level window" is undefined, and a real install conflicts with it.** B and C exempt "second-story / upper-floor" windows, which implies ground-level means ground floor. Houston's right-hand battery sits partly under a ground-floor window with a ~4.5–5 ft sill. Under C that is prohibited unless the window is fixed, or unless Base means at-grade (basement-level) windows only. Field practice may be more lenient than the prompt. Ask Base; until then label per the prompt text.
33. **[new v0.2] Profile A is underspecified.** The source has no output block, no headroom, ground or facing rule, and no door or garage rule (only "clear, continuous wall; windows, columns, and electrical equipment break the wall"). It also says "a pool" with no kiddie exemption and omits gas pipes. A reads as an earlier version of B without the ComEd window rule.

### 7.3 Between Base's rules and what the team built

21. **The only real-geometry placement test mixes profiles.** PR #13's ETH3D case expects `manual_review` because no window gap fits 2.58 + 3 + 3 = 8.58 ft. That is B's 3 ft opening rule applied to C's 31 in battery with a 20 ft route cap from neither. Under C, the 5.1–5.7 ft window gaps do hold a 31 in battery; see the ETH3D labels in `docs/house-labels.md`.
22. **Server placeholders (PR #11) match no profile:** gas 3 ft (C), drive 5 ft (A/B), pool 10 ft (C), route ≤ 20 ft (none), confident reach 15 ft (A), openings 3 ft (B), headroom 6.5 ft.
23. **Server rules Base doesn't have:** 3 ft from AC (Base treats HVAC as a fixed footprint and lets the harness pass behind it) and 3 ft from fences (Base has only the 5 ft facing gap).
24. **Base rules the server lacks:** street-facing review, main/secondary walkway, driveway and garage crossing (partly), over-door review, movable-object `requires_member_action`, meter-access reject, disconnect and conduit space (A/B).
25. **Public baseline** (NEC 110.26: 30 in wide × 36 in deep × 6.5 ft) vs P12 envelope (2.5 × 3 ft × 6 ft review line): consistent except 6 vs 6.5 ft headroom.
26. **Context doc wording:** it says every check returns pass / review / reject. A/B cannot reject; P12 uses inverted yes/no (`yes` is a reject, so that part is close); P5 and the feature prompts return facts. It also phrases P12 gas "more than ~3 ft away" as a requirement; in the source, gas within 3 ft only triggers review.
27. **[confirmed] Notion main page** says the cable "can't cross a door". That is true for B (never, over or below). C allows over-door with review and prohibits below/across.

### 7.4 Default until Base answers

- **Evaluate and report all three profiles side by side.** Labels in `docs/house-labels.md` are per profile.
- For a single safe answer, use a **strict composite S**: every threshold at its strictest across A/B/C (gas 5, drive 5, pool 10, 3 ft openings, windows block the route, no door crossing, same-wall reach 15 ft, 5 ft facing, 7 ft headroom, 31 in width, 39.5 in height, A/B disconnect space). An S-pass is a pass under every profile. **S-reject = C-reject** (only C can reject, with C's own thresholds). Everything else is `manual_review`. This matches the pipeline plan's "strictest" default without inventing a reject rule.

## 8. Questions for Base

1. Which profile applies to which utility and hardware? B is stated as ComEd; is C current for Texas and everything non-ComEd, and is A retired?
2. One consistent threshold set: gas, drive, pool, openings, reach (and whether reach is straight-line, wall run or routed cable), battery size.
3. Does the 16 ft review trigger relax when capture is metrically measured?
4. Missing brand label: review or pass?
5. How do per-check results combine into a site outcome, and what do CL320, oversized can and pole mount change?
6. Headroom and slope numbers.
7. Bushes in the meter working envelope: movable or fixed?
8. Is a P12 `yes` a site reject even when placement passes, or only a flag?
9. Does "ground-level window" mean ground floor or at-grade? The Houston install has a battery under a ground-floor window.
10. Meter Type: is the head counted in "2 meter-heads tall"? (#29)
11. Main Panel Type: is a closed second enclosure beside a visible main a pass or a review? (#30)
