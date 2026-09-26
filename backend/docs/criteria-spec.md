# Criteria spec: Base Power site-survey (SSR) checks

Status: draft v0.1, 2026-09-26. Owner: criteria worker (Track C). For Hunter to review.

## 0. Source and how to read this

- **Source used:** Hunter's Notion digest of all 13 SSR prompts ([Site Survey Criteria — Quick Reference](https://app.notion.com/p/3e7071360df881329440ff267511e13d), edited 01:07 UTC), copied to `internal/criteria/ssr-quick-reference-notion.md`.
- **The original `SSR_Prompts.md` could not be downloaded.** The Notion MCP exposes the attachment only as an internal reference with no signed URL, and the file is not in Slack, Drive or the repo. Exact output schemas, field names and any wording the digest dropped are therefore unconfirmed. To unblock: drop the file at `internal/criteria/SSR_Prompts.md`, or paste it into a Notion page body. Details in `internal/criteria/README.md`.
- **Machine-readable thresholds:** `internal/criteria/criteria.yaml`.
- All distances are feet unless stated. "~" means the prompt says "about/roughly".
- **Modality** says what kind of evidence can decide a check:
  - **G (geometric):** needs a metric measurement or spatial relation, so a metric 3D scene, AR taps or LiDAR. Photo guesses are unreliable here: the Notion page says the prompts themselves note that models overestimate gaps by ~2×.
  - **P (photo):** scene semantics from ordinary views (what something is, its state, its context). No metric measurement.
  - **R (close-up read):** needs a legible close-up (text, breaker markings, fine condition).
  - Combinations (G+P) mean the object must be recognised in photos and then measured in 3D.

## 1. Outcome vocabulary (not uniform across prompts)

| Prompt family | Output values | Notes |
|---|---|---|
| Placement C, Main Panel Type, Main Panel Recall | `pass` / `manual_review` / `reject` | The only prompts that can reject. |
| Placement A and B | `pass` / `manual_review` | **No reject.** Any failure or doubt goes to review. |
| Meter–Panel Location (4 fields) | `yes` / `no` / `manual_review` | **Inverted polarity:** `yes` = problem present. `notSameWall` never returns `yes`. |
| Feature extractors (P1, P2, P3, P5, P10, P11, P4) | value + `clean` or `insufficientEvidence` flag | Facts only; they never approve or reject on their own. |
| All | `needs_more_photos` + `missing_views[]` | Separate from `manual_review`. A visible borderline gap needs a measurement, not more photos. |

**Site outcome is undefined by Base** (open question). Proposal for our scorer, not Base policy:
1. `reject` if any decision prompt rejects (Placement C, Main Panel Type, Main Panel Recall).
2. Else `manual_review` if any decision prompt or P12 field is `manual_review`/`yes`, any needed feature is unclean, or views are missing.
3. Else `pass`.
Feature prompts and P5 inform people and other prompts; they never change the site outcome by themselves.

## 2. Evidence catalogue

| Base checklist photo (public, 9) | Packet `purpose` | Feeds |
|---|---|---|
| Meter close-up | `meter_close` | P4 meter number, P3 CL320, P2/P11 can size (needs both can edges) |
| Meter surroundings, 10+ steps back | `meter_surround` | P10 mount, P12 meter access, placement |
| Left and right of the meter | `meter_left`, `meter_right` | placement (candidate spots, openings, gas, drive) |
| Adjacent wall, corner to corner | `adjacent_wall` | placement (wall ends, needed for a C reject) |
| Behind the fence | `behind_fence` | placement (side yard, gate, pool) |
| Main breaker box (open) | `panel_open`, `panel_close` | P13 recall/condition, P1/P9 mains |
| Main disconnect with amperage | `main_disconnect` | P1/P9 (which breaker is the service main) |
| Breaker-box surroundings | `panel_surround` | P12 panel access and panelInHouse, P5 relationship |

**3D observation bands placement needs** (derived from thresholds; see §4.4): meter wall to both natural ends; ground out to ~12 ft from the wall (C pool) or ~7–9 ft (A/B pool and drive); facing to ≥ 5 ft; overhead to ≥ 7 ft above the footprint.

## 3. Summary of the 13 prompts

| # | Source prompt | Our id | Manifest group | Question | Output | Modality |
|---|---|---|---|---|---|---|
| 1 | True Multiple Main Panels | `multiple_mains` | panel_count | Two or more independent service mains? | bool + layout + insufficientEvidence | P+R |
| 2 | Large or Oversized Meter Can | `oversized_can` | meter_enclosure | Is the meter-holding can oversized? | bool / `meter_can_unclear` | P |
| 3 | CL320 Meter | `cl320` | cl320 | Does readable text say CL320? | bool / `cl320_unclear` + sufficiency | R |
| 4 | Meter Number | `meter_number` | meter_identity | Utility meter number, and is the read clean? | string + clean | R |
| 5 | Meter–Panel Relationship | `meter_panel_relationship` | meter_panel_relationship | back_to_back / not_back_to_back / co_located_exterior / insufficient | subtype + confidence | G+P (cross-view) |
| 6 | Battery Disconnect Placement | `placement.A` | placement | Profile A spot | pass / review | G+P |
| 7 | Battery Disconnect Placement — 3 ft Window Rule | `placement.B` | placement | Profile B spot | pass / review | G+P |
| 8 | Battery Space | `placement.C` | placement | Profile C spot + recorded fields | pass / review / reject | G+P |
| 9 | Main Panel Type | `main_panel_type` | panel_count | Decision on mains | pass / review / reject | P+R |
| 10 | Meter Mount | `meter_mount` | meter_enclosure | standard / pole / bank | class + clean | P |
| 11 | Meter Type | `meter_type` | meter_enclosure | standard / oversized / undersized / combined | class + clean + can ratio | P (single-view ratio) |
| 12 | Meter–Panel Location | `meter_not_accessible`, `panel_not_accessible`, `panel_in_house`, `not_same_wall` | equipment_access | Four problem flags | yes / no / review each | G+P |
| 13 | Main Panel Recall | `panel_recall` | panel_condition | Replacement or review triggers | pass / review / reject | R+P |

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
| Gas (meter, regulator, shutoff, pipes) | 5 ft radius | 5 ft | ~3 ft from every part of battery (≈ 6 ft contiguous wall) | G+P |
| Drivable surface | 5 ft radius, +1–2 ft caution if drive wraps | 5 ft | ~3 ft beyond footprint, +1–2 ft caution if drive wraps | G+P |
| Permanent pool | 5 ft | 5 ft | ~10 ft; doubt → review; auto-pass only if clearly in a separate area. Kiddie pools exempt | G+P |
| Facing clearance (wall → fence/hedge/wall) | not stated | not stated | ≥ ~5 ft including battery depth; borderline → review | G |
| Headroom | not stated | ~6–7 ft | ~6–7 ft; eaves, stairs, decks, landings fail | G |
| Ground | not stated | dirt/lawn/gravel/mulch/concrete, mild slope; decks or extreme slope fail | same plus planter bed; steep concrete fails | P (type) + G (slope, no numeric limit) |
| Openings beside battery | keep wall area clear of windows (no number) | ≥ 3 ft horizontally each side of door, window well, operable ground-level window; two openings need ≥ ~8 ft gap (≤ 7 ft fails) | don't sit under or block operable ground-level windows, wells, doors; leave a few inches; keep swing | P (operable, level, well) + G |
| Exempt openings | – | upper-floor and fixed windows | upper-floor and fixed windows | P |
| Windows on cable route | block the route | harness may pass above/below | harness may pass above/below | P+G |
| Door on route | needs clear continuous wall | never, including above a door | above door → review; below/across → prohibited | P+G |
| Garage on route | blocked (continuous wall) | prohibited | prohibited | P+G |
| Driveway crossing | – | – | prohibited | P+G |
| Other route rules | columns and electrical equipment block | over thin pipes/lines OK | 1 in cable on building wall, support every ~3 ft; above/below windows, behind AC, over small pipes OK; no loose ground run or unsupported gap; fence post: 2 in behind or drill ~4.5 in post | G+P |
| Disconnect / conduit | ~2 ft open wall beside meter, same wall; below meter free | 2–2.5 ft near meter (above OK); clear exit below + ~6 in bend room; boxed-in meter → review | excluded | G (6 in is below learned-depth accuracy; needs AR tap or close-up) |
| Pedestal meter | – | allowed if conduit reaches a nearby wall | – | P |
| Walkways | – | – | keep main entrance path clear; only spot on secondary walkway → review | P+G |
| Service access | keep clear of electrical equipment | HVAC/permanent equipment blocks its footprint | may sit beside equipment with one accessible side; not covering fronts; not directly under a wall-mounted electrical/comms box or vent | P+G |
| Street-facing front | – | – | only workable spot on street-facing front → review | P (+GPS/map) |
| Meter access (route start) | – | boxed-in → review | normal standing room; clearly sealed off → can reject; doubt → review | G+P |
| Decision | pass only if every check is confident; else review | pass if a spot + disconnect + route all work; else review | pass / review / reject (§4.3) | – |

**Movable vs fixed (B and C):** plants, bushes, planters, bins, boxes, furniture, grills, hoses, play equipment, kiddie pools, downspouts, pipes, hose bibs, small utility boxes, a single gate or short fence section are movable (set `requires_member_action`). HVAC, permanent electrical equipment, gas equipment, operable windows and wells, permanent pools and a whole parallel fence run are fixed.

### 4.3 Profile C decision rules and recorded fields

- **Pass:** at least one clearly shown spot meets every rule, with a visible feasible route ≤ ~16 ft and no review-only condition.
- **Manual review:** marginal measurement; footprint, ground or route obscured; pool or window-well doubt; route over a door; only spot street-facing or on a secondary walkway; route > ~16 ft.
- **Reject:** every reachable candidate clearly fails and the wall is seen to its natural end both ways. Record both endpoints and why each candidate fails.
- **Photo calibration (C):** ~1 ft from gas or drive is clearly too close; an uncertain 2–6 ft gap goes to review. With metric capture, use the margin rule instead: pass if measured − error > T, fail if measured + error < T, else review (PR #11/#13 convention).
- **Recorded fields:** `reference_object`, `distance_ft` (routed length), `front_clearance_ft` (99 = wide open), `drive_path_ft` (null if none), `requires_member_action`, `decision`, `needs_more_photos`, `missing_views[]`, `justification`.

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
- **Read:** prefer the utility-facing identifier over the manufacturer serial. Exclude kWh readings, class/form, volts/amps, model/FCC/UL ids, addresses, account/ESI numbers, panel labels, notes. Strip barcode wrappers.
- **Clean = false:** multiple/duplex meters, cropped meter or label, glare or scratches, obstruction, tiny or distant digits, no electric meter.
- **Output:** best-effort number (empty string only if no meter number is photographed), `clean`, reasoning. An unclean suggestion is never a trusted id.

### P3 CL320 Meter (`cl320`) · R
- **True only if** readable meter-face or label text explicitly says CL320. CL200 / Class 200 / FORM 2S CL200 is not CL320. Can size or breaker rating never substitutes.
- **Unreadable:** `cl320_unclear`, insufficient evidence.
- Role in the site outcome is not stated (open question).

### P2 Large or Oversized Meter Can (`oversized_can`) · P
- Judge the enclosure directly holding the round meter, not a neighbouring disconnect or load centre. A CL200 meter can sit in an oversized can.
- **True:** visibly much larger than a compact socket, or a large 320A-style can. Size unknown → `meter_can_unclear`.

### P11 Meter Type (`meter_type`) · P
- **Ratio** = full can height ÷ round glass-head height, from **one photo** in 0–1000 normalised vertical coordinates. Both can edges visible; head ≥ ~10 units. No usable view → coordinates 0, unclean. An image heuristic, not feet.
- **standard** ≈ 2 heads; **oversized** ≈ 3–4 heads with substantial metal above/below (a borderline ~3 is unclean, not a hard cut); **undersized** ≈ 1–1.5 heads; **combined** = meter and main breaker in one unit.
- **Clean = false:** multiple meters, poor visibility, no single view with both can edges and the head, ambiguous form, no can. Still give a best guess; `unknown` only if genuinely indeterminable.
- 3D could supply a metric can height, but the prompt defines the check as the single-view ratio.

### P10 Meter Mount (`meter_mount`) · P
- **standard:** single meter on the home's exterior wall. **pole:** standalone pole. **bank:** clear in-line row (≥ 3 is a strong signal).
- **Clean = false:** mounting context missing or obscured, extreme angle or distance, improvised or propped support, two separated meters or uncertain alignment. A clear bank is a clean mount even though it makes P4 and P11 unclean.

## 6. Panel and relationship prompts

### P1 True Multiple Main Panels (`multiple_mains`) · P+R
- **True** only with ≥ 2 panels, each with a clearly visible independent main breaker/disconnect and evidence each is an independent service main.
- Not counted by appearance: adjacent closed boxes, transfer switches, emergency disconnects, meter-main equipment, solar equipment, downstream panels, a prominent 2-pole breaker alone. One feeding the other = main + downstream.
- Unknown relationship → unclear / `insufficientEvidence`. "Not proven multiple" ≠ "single proven".

### P9 Main Panel Type (`main_panel_type`) · P+R
- Confirmed true multiple mains → **reject**; confirmed single main → **pass**; genuine doubt (separate locations, no open view, glare, hidden breakers) → **review**.

### P5 Meter–Panel Relationship (`meter_panel_relationship`) · G+P
- Facts, not a decision: panel location (garage interior / exterior / non-garage interior / not visible / unknown); meter relative to garage (beside the opening / other garage wall / non-garage wall / not visible / unknown); subtype `back_to_back` (panel on the inside face of the meter's wall), `not_back_to_back` (e.g. 90° garage return wall; "same garage" is not enough), `co_located_exterior`, `insufficient_evidence`; confidence; rationale.
- Anchors: windows, garage-door edges, corners, interior wall returns, conduit.
- **Geometric by nature:** it asks which wall face the panel sits on. An exterior-only reconstruction cannot settle it. If the iOS capture keeps **one AR session** from the meter to the panel, the relative pose settles it directly; otherwise only visual anchors can.

### P12 Meter–Panel Location · G+P
Working envelope per device: ~3 ft out from the face × 2.5 ft wide centred; overhead obstruction below ~6 ft → review; gas meter/regulator within ~3 ft → review.

| Field | `no` (pass) | `manual_review` | `yes` (problem) |
|---|---|---|---|
| `meter_not_accessible` | clear envelope; trivial movable items and building structure ignored | substantial storage, fixed shelf, low headroom, nearby gas, frontage unseen | fixed obstruction in envelope: bush/overgrowth, AC condenser, water heater |
| `panel_not_accessible` | same | same | fixed obstruction, or panel walled/boxed off |
| `panel_in_house` | clearly in garage or outside | garage vs living space unclear; cropped/dark | clearly in living space (closet, hallway, room, laundry, finished basement, attic) |
| `not_same_wall` | both plainly outside on the same exterior wall | inside, different walls, or not proven | never |

A shared object across photos does not prove the same wall.

### P13 Main Panel Recall (`panel_recall`) · R+P

| Finding | Result |
|---|---|
| Brand text confirms FPE / Federal Pacific / Stab-Lok, Challenger, Zinsco / GTE-Sylvania / Zinsco-Sylvania / Magnetrip, Sylvania | reject (read text, not appearance) |
| Heavy or widespread corrosion on enclosure, dead-front or breakers | reject |
| Any vertically operating breaker with up = OFF, down = ON | reject (horizontal handles exempt; unreadable → review) |
| Westinghouse; light or uncertain rust; old look; exposed/loose wiring or missing dead-front; old/rusted separate disconnect | review |
| Missing or unreadable brand, ambiguous evidence | review (the source also has a looser "no visible recalled brand" pass phrase; unresolved) |
| Modern panel, none of the above, evidence sufficient | pass |

## 7. Reconciliation with the three rule versions and other rule sets

Compared: Base profiles A/B/C (above), `internal/house-scanning-context.md` (its summary of the same prompts), the repo's public baseline (`docs/01`, `docs/04`), and the PR #11 server `rules.yaml` placeholders. The context doc's A/B/C table matches the digest on every number; the conflicts below are between profiles, within the source, or between Base and what the team built.

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

B is labelled ComEd-specific (Illinois). Base operates mainly in Texas, so B likely applies only to ComEd territory (inference; confirm).

### 7.2 Within the source (ambiguities to resolve with Base)

12. **Bushes:** movable (`requires_member_action`) in B and C, but an overgrown bush in the working envelope is a fixed obstruction (`yes`) in P12.
13. **Missing panel brand:** "missing/unreadable → review" vs a looser "no visible recalled brand → pass".
14. **Headroom** is a range (~6–7 ft), not a threshold. We use: < 6 ft fail, ≥ 7 ft pass, between → review unless measured with confidence.
15. **Slope** has no number ("mild" vs "extreme/steep").
16. **Drive distance origin:** "~3 ft beyond the footprint, in addition to battery depth" means ≥ 3 ft from the battery's edge (≈ 4.8 ft from the wall). The server measures from the footprint too; confirm.
17. **Meter Type** 3-head borderline is both "oversized" and "unclean".
18. **Pole meter:** P10 classifies it; only B addresses eligibility (allowed if conduit reaches a wall). Under C a harness may not run loose over ground, so a pole meter may be infeasible; unstated.
19. **Role of CL320, oversized can and meter number** in eligibility is never stated.
20. **The "models overestimate gaps ~2×" note** appears on the Notion main page but not in the digest; unverified against the original.

### 7.3 Between Base's rules and what the team built

21. **The only real-geometry placement test mixes profiles.** PR #13's ETH3D case expects `manual_review` because no window gap fits 2.58 + 3 + 3 = 8.58 ft. That is B's 3 ft opening rule applied to C's 31 in battery with a 20 ft route cap from neither. Under C, the 5.1–5.7 ft window gaps do hold a 31 in battery; see the ETH3D labels in `docs/house-labels.md`.
22. **Server placeholders (PR #11) match no profile:** gas 3 ft (C), drive 5 ft (A/B), pool 10 ft (C), route ≤ 20 ft (none), confident reach 15 ft (A), openings 3 ft (B), headroom 6.5 ft.
23. **Server rules Base doesn't have:** 3 ft from AC (Base treats HVAC as a fixed footprint and lets the harness pass behind it) and 3 ft from fences (Base has only the 5 ft facing gap).
24. **Base rules the server lacks:** street-facing review, main/secondary walkway, driveway and garage crossing (partly), over-door review, movable-object `requires_member_action`, meter-access reject, disconnect and conduit space (A/B).
25. **Public baseline** (NEC 110.26: 30 in wide × 36 in deep × 6.5 ft) vs P12 envelope (2.5 × 3 ft × 6 ft review line): consistent except 6 vs 6.5 ft headroom.
26. **Context doc wording:** it says every check returns pass / review / reject; A/B cannot reject, P12 uses inverted yes/no, and P5 and the feature prompts return facts. It also phrases P12 gas "more than ~3 ft away" as a requirement; in the source, gas within 3 ft only triggers review.
27. **Notion main page** says the cable "can't cross a door"; C allows over-door with review.

### 7.4 Default until Base answers

- **Evaluate and report all three profiles side by side.** Labels in `docs/house-labels.md` are per profile.
- For a single safe answer, use a **strict composite S**: every threshold at its strictest across A/B/C (gas 5, drive 5, pool 10, 3 ft openings, windows block the route, no door crossing, same-wall reach 15 ft, 5 ft facing, 7 ft headroom, 31 in width, 39.5 in height, A/B disconnect space). An S-pass is a pass under every profile. **S-reject = C-reject** (only C can reject, with C's own thresholds). Everything else is `manual_review`. This matches the pipeline plan's "strictest" default without inventing a reject rule.

## 8. Questions for Base

1. Which profile applies to which utility and hardware? Is C current for Texas, and B only for ComEd?
2. One consistent threshold set: gas, drive, pool, openings, reach (and whether reach is straight-line, wall run or routed cable), battery size.
3. Does the 16 ft review trigger relax when capture is metrically measured?
4. Missing brand label: review or pass?
5. How do per-check results combine into a site outcome, and what do CL320, oversized can and pole mount change?
6. Headroom and slope numbers.
7. Bushes in the meter working envelope: movable or fixed?
