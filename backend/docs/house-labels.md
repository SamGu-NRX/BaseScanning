# House labels (draft ground truth, for Hunter to approve)

Status: draft v0.5, 2026-09-26. v0.5 rechecks the facade with the laser-read w2 window; no expected answer changes. v0.4 adds `eth3d-delivery`, the first site with start-range labels. Labelled against the original `internal/criteria/SSR_Prompts.md`, via `docs/criteria-spec.md` v0.2. Covers all 7 `ready` houses in `internal/data/manifest.json` (manifest generated 16:21 UTC).

**Changes in v0.3** (after the photo-baseline review, `internal/breadth/C3-vlm-baseline.md`):
- **`yt-12Ks1idWj40` panel labels corrected.** v0.2 called the grey enclosure closed and unidentified, working from `t0000.00` only. In fact it's open in `t0003.00` (dead-front on, breakers and a top main visible) and in `t0009.00`–`t0012.00` (dead-front off during the install). It is the breaker panel, on the exterior wall right beside the meter. Revised labels:
  - Now `no`: `panel_in_house`, `not_same_wall`, `panel_not_accessible`.
  - P5: `co_located_exterior`.
  - `multiple_mains`: `single_main_plus_disconnect_or_subpanel`.
  - `main_panel_type`: review or pass both acceptable.
  - `panel_recall`: stays review, because the brand is unreadable.
- **`yt-12Ks1idWj40` is excluded from placement scoring.** Frames after `t0000.00` show the finished Base battery, so the packet leaks the answer. The placement labels describe `t0000.00` alone. In the `labels-v0` exports, placement truth is `unknown` and `site_decision` is `unknown`.
- **`eth3d-facade` placement C: pass is now acceptable alongside review.** Confidence is low, so the export marks it `unknown` (not scored). The geometry passes, and both review triggers turn out to be weak on this building.

**Files**
- `internal/criteria/house-labels.json`: the full labels. Per house, per criterion: truth, expected output in the source's own keys and enums, acceptable outputs, evidence frames, confidence, and note.
- `internal/criteria/labels-v0/<site>.<profile>.json`: 28 files in the scoring harness's `base-labels/v0` format (`docs/scene-and-verdict-format.md` §6.2), all validated against the schema.
  - Mapping: expected `pass` or P12 `no` → `pass`; `reject` or P12 `yes` → `fail`.
  - Expected review, unclean or insufficient → `borderline` (a correct path says unsure).
  - Low confidence, or both review and pass acceptable → `unknown` (not scored).
  - Placement for a house flagged `placement_scoring.usable: false` → `unknown`.
  - These are site-scope labels only. Start-range labels need a scene with a real or virtual meter.
  - Regenerate with `python3 internal/criteria/export_labels_v0.py`.

## How to read

- **Truth** is what a careful person can determine from the frames: `undeterminable` when the frames can't settle it, `not_applicable` when the object doesn't exist. Nothing is guessed.
- **Expected** is what a correct SSR prompt should output given only this imagery; this is what gets scored.
- **Placement** is labelled for profiles A, B, C and the strict composite S (spec §7.4). Base hasn't said which profile applies, and B is ComEd-only.
- **Site** uses the proposed combination rule from spec §1: reject if C, Main Panel Type or Recall rejects, or if any P12 field is `yes`; otherwise review on any review, unclean or missing result.
- Frame ids refer to `internal/data/houses/<id>/frames/`. Confidence levels: H = high, M = medium, L = low.

## Summary

| House | What it is | Placement A / B / C / S | Meter checks | Panel checks | Site |
|---|---|---|---|---|---|
| `eth3d-facade` | Real facade, laser GT; no meter (PR #13 invents one) | review ×4, conditional on the invented meter (C: pass also acceptable) | n/a | n/a | review |
| `eth3d-electro` | Real utility building, laser GT; no meter | review ×4 | n/a | n/a | review |
| `tt-barn` | Real park building, laser GT; no meter | review ×4 | n/a | n/a | review |
| `eth3d-delivery` | Real indoor loading garage, laser GT scene; meter invented, AC synthetic | review / review / **reject** / **reject** (review acceptable for C and S) | n/a | n/a | reject |
| `yt-12Ks1idWj40` | Real Base install, Houston | review / review / **pass** / review on `t0000.00`; **excluded from placement scoring** (answer leak) | type, mount and can labelled; number and CL320 unreadable | panel outside on the same wall: location, access and P5 labelled; recall review | review |
| `yt-5-M-7f35Tyg` | Midwest, winter, 360×480 | review ×4 | mount labelled; others weak or unreadable | undeterminable | review |
| `yt-0gotY_syWWU` | Rural Southeast loop | review ×4 (meter unconfirmed) | undeterminable | undeterminable | review |
| `yt-4h8kyjnPo9s` | Brick bungalow | review ×4 (meter not found) | undeterminable | undeterminable | review |

- No house should produce a `reject` under any profile, and no P12 field is `yes` anywhere.
- Only one placement `pass` is labelled: Houston under profile C. It isn't scorable, because that house's packet leaks the answer.
- Only Houston shows an open panel (a time-lapse at a distance). Every other house's panel checks (`multiple_mains`, `main_panel_type`, P5, `panel_not_accessible`, `panel_in_house`, `not_same_wall`, `panel_recall`) are undeterminable, with expected outputs `unclear_or_insufficient_evidence`, `insufficient_evidence` or `manual_review`.
- The three laser-GT sites have no meter, so their placement checks can only be scored at start-range level once someone defines a virtual meter.

**Expected feature outputs when there is no meter or panel** (the three laser-GT sites, and the two houses with no confirmed meter):
- `meter_number` "" with `clean-decision` false
- `cl320Label` `cl320_unclear` with `insufficientEvidence` true
- `meterCanLabel` `meter_can_unclear`
- Meter Type: `type` `unknown`, all four y-coordinates and `distance_ratio` 0, not clean
- Meter Mount: `type` `unknown`, not clean
- `multipleMainPanelLabel` `unclear_or_insufficient_evidence`
- P5 `meterPanelRelationship` `insufficient_evidence` (`not_applicable` also acceptable for non-houses)
- `main_panel_type`, `panel_recall` and all four P12 fields `manual_review`

## eth3d-facade

A real building with laser ground truth. It isn't a house and has no meter or panel, so every electrical truth is `not_applicable` (H).

**Placement, conditional on PR #13's invented meter** (wall w1, s = 0, 5 ft up, between windows 4 and 5):

| Profile | Expected | Conf | Why |
|---|---|---|---|
| C | manual_review (**pass also acceptable**, v0.3) | L | The geometry passes. The gap between windows 3 and 4 is 5.13 ft, which holds a 31 in battery a few inches clear of both windows. The route is about 10–11 ft and passes below window 4 (sill 3.32 ft), which C allows. The front is open 60 ft (`front_clearance_ft` 99), on flat gravel, with no overhang, gas or pool. It still goes to review for two reasons: w1 is the street-facing entrance side, and whether a car can reach the courtyard can't be determined (connected gravel and paving; no clear vehicle gate in DSC_0366 or DSC_0414). The meter's own gap can't take the battery, which would also sit under the meter box. |
| B | manual_review | H | No w1 gap reaches the 8 ft needed between openings. w2 has an arched ground-floor window (v0.5): 3 ft past it leaves only 2.1 ft before the outside corner, marginal for a 2 ft battery. |
| A | manual_review | H | Windows block the route, and the meter's gap would need to hold meter + disconnect + battery with about zero margin. |
| S | manual_review | H | – |

**Why pass is now also acceptable under C (v0.3).** Both review triggers are weak on this building:
- **Drive:** the only cars are on the street beyond the courtyard's fence and tree line (DSC_0366, DSC_0386). No vehicle gate appears, so the gravel isn't shown to be reachable by car.
- **Street-facing:** the "front of the home needs member sign-off" rule is written for a homeowner's aesthetics. Its application to a set-back entrance facade on a non-residential building is ambiguous.

Review stays the expected answer because the prompt's literal text favours it. A pass that argues both points (as GPT-5.5's does) isn't counted as unsafe: confidence is low and the export marks it `unknown`.

**v0.5 recheck: the w2 arched window changes no expected answer.** The laser shows an arched ground-floor window on w2 (u 1–6.5 ft, sill 3.5 ft) that PR #13 and the first harness scene missed. For Base's profiles:
- **A:** uses the same wall (w1) only, so w2 doesn't matter.
- **B:** can't reject, and w2 offers no clean spot anyway. The C3 GPT-5.5 B pass that put the battery on w2 is now clearly wrong.
- **C:** passes on w1 between windows 3 and 4. C1 finds 11 passing positions there with the window in the scene. Because a passing spot exists, C doesn't reject.
- **S:** rejects only when C does, so it stays review.

The "reject under every ground rule" result comes from C1's public profile, which uses **PR #11's demo rules** (verdict `rules_profile: demo`). Under those rules a 3 ft opening rule fails both w1 and w2, and w3 is out of route reach. Base's labels don't define a demo answer. Scoring that verdict against these A/B/C/S labels therefore reports a spurious "missed review". It belongs against the harness's own demo labels (`internal/eval-harness/labels/eth3d-facade.demo.labels.json`).

PR #13 expects `manual_review` and that is right, but under C it's right for a different reason than PR #13 gives (spec conflict #21). Evidence: DSC_0419, DSC_0420, DSC_0362, DSC_0366, DSC_0386, DSC_0414, DSC_0391, DSC_0393.

## eth3d-electro

An institutional/utility building near Zurich with laser GT and no meter. Placement truth is `not_applicable`. The expected output is `manual_review` with `needs_more_photos` (meter location) under every profile (H); a reject would be wrong without a meter.

Notes for a future virtual-meter test:
- On the long steel-door wall, every bay is a double door under a canopy (DSC_9269, DSC_9272).
- Car-accessible asphalt runs to the wall base, so every spot there fails the drive rule. Under C, being within ~1 ft of a drive is a confident fail.
- The only non-drivable ground is a grass strip along the glass facade by the cable mast (DSC_9263). So a door-wall meter gives a C reject only if that strip is out of reach (beyond ~26 ft, or only reachable over a door).
- The free-standing louvred enclosure (DSC_9266) is fixed equipment.

## tt-barn

A single-storey park building (restrooms and storage) in California, with laser GT and a full loop with every corner seen. There's no meter; the small boxes by the gable-end doors are drinking fountains (000343). Placement truth is `not_applicable`, and the expected output is `manual_review` with `needs_more_photos` under every profile (H).

Notes for a future virtual-meter test:
- The gable end has a porch roof over a recessed doorway (000343). Spots there fail on headroom and door swing.
- The roll-up doors count as garage doors, so the route can't cross them.
- Whether the concrete aprons and plaza in front of those doors are drivable can't be determined (000028, 000040). If they are, C sends it to review and A/B fail the 5 ft rule.
- The long side walls have man doors, lattice screens, and a narrow concrete walk with open lawn beyond (000109, 000136). A C spot on the lawn beside the walk is plausible there.

## eth3d-delivery (v0.4)

ETH3D `delivery_area`: an indoor, underground loading garage. It has no manifest entry. It's labelled because the harness has a ground-truth scene for it (`internal/eval-harness/results/eth3d-delivery/gt-laser/scene.json`), built from the C2 equipment file `internal/breadth/c2-volumetric/scenes/eth3d-delivery-garage.json`.
- **Meter:** invented, on wall w1 at s = 0, 1.5 m up, on the jamb between the sliding door and the corner.
- **AC:** synthetic.
- **Photos:** `internal/breadth/c2-volumetric/results/viz/delivery_area_photos.jpg`.

Electrical checks are `not_applicable` (there's no imagery of the meter or a panel), with the standard no-meter outputs. `meter_not_accessible` is `manual_review`, because the synthetic AC sits about 6 cm outside the 3 ft working envelope.

**Placement: every position fails, under every profile.**
- **Drive:** the concrete floor is continuous and has vans and a car parked on it, so every spot is on a drivable surface (0 ft). The scene's ground region is labelled `concrete`, not drive, so any path that trusts region material will pass drive clearance wrongly. The labels catch that as unsafe.
- **Indoors (C and S):** "the battery can never be installed inside the home"; C requires an exterior wall.
- **Wall ends:**
  - Left of the meter, the 8.4 m sliding garage door (w1, s −9.30 to −0.90) breaks the route.
  - Right of it, the corner is at s 0.86, and back wall w2 is observed past the 26 ft (7.92 m) cable limit.
- **Other w2 constraints:** the AC at s ~1.8–2.6; facing gaps of 0.3–0.7 m at s 3.0–7.3 and 9.7–10.6; a mesh storage cage 8 cm off the wall at s 7.3–13.8; low overheads (1.27–1.83 m) at s 4.5–7.6 and 9.7–10.6.

| Profile | Site label | Start-range labels (w1 s −9.30…0.86, w2 s 0.86…19.28) | Conf |
|---|---|---|---|
| A, B | `manual_review` (export: `borderline`). These profiles can't reject, so a site-level reject counts as a missed review. | `placement` fail, `drive_clearance` fail (0 ft) | H |
| C, S | `reject`, review acceptable (export: `not_pass`, `site_decision` reject). Both wall directions end at natural endpoints. Review is acceptable because the meter is invented and C2 found an unobserved recess near the corner, though it too would be indoors on the drivable floor. | `placement` fail, `drive_clearance` fail (0 ft) | M |

## yt-12Ks1idWj40 (Houston, CenterPoint)

A fixed Wyze camera: `t0000.00` shows the bare wall before the install and `t0078.01` the finished install. Measurements are from t0000, scaled from the meter glass (~7 in):
- Corner to meter can: 3–3.75 ft.
- Grey enclosure's right edge to the window: 4.5–5.7 ft.
- Eave: about 8 ft. Open lawn more than 12 ft deep.
- No drive, pool or gas in view. The far side of the left corner, where the AC sits, is unseen.

**Not usable for placement scoring.** The packet is the install time-lapse, so every frame after `t0000.00` shows installers, equipment and finally the finished battery, and models cite them. The placement rows below describe `t0000.00` alone. Score placement only on a packet restricted to that frame. The exports set placement truth and `site_decision` to `unknown`.

**The panel is visible.** The grey enclosure is open in `t0003.00`, with its dead-front on: two columns of branch breakers under a horizontal main-style breaker at the top, and label cards on both sides. It's open again in `t0009.00`–`t0012.00` with the dead-front off during the install. It is the breaker panel, on the exterior brick wall immediately right of the meter, and the only panel pictured. The brand is unreadable at this distance.

**What Base actually installed:** a white BASE disconnect to the right of the grey enclosure, and two batteries. One sits between the left corner and the meter; the other sits on the right, **partly under the window** (sill about 4.5–5 ft).

| Criterion | Truth | Expected (source keys) | Conf | Note |
|---|---|---|---|---|
| placement.C | pass | `pass` (review acceptable); `distance_ft` ~2–4, `front_clearance_ft` 99, `drive_path_ft` null, `requires_member_action` false, `needs_more_photos` false | M | The spot is between the enclosure and the window. It isn't under the box or the window, and the footprint, ground and route are all visible. |
| placement.B | manual_review | `manual_review` | M | Labelled for comparison only, since B is the ComEd profile. The right-hand spot needs 2 ft battery + 3 ft to the window, i.e. 5 ft, against 4.5–5.7 ft available: marginal. The left spot is beside an unseen corner (5 ft gas radius). The service riser occupies the "clear exit below the meter". |
| placement.A | manual_review | `manual_review` | M | The left side needs 4 ft and has 3–3.75 ft. Reaching the right spot means routing past electrical equipment, which A treats as a blocker. |
| placement.S | manual_review | `manual_review` | M | – |
| meter_number | undeterminable | any best-effort `meter_number`, `clean-decision` false | H | The meter is in frame but the digits are too small to resolve. |
| cl320 | undeterminable | `cl320_unclear`, `insufficientEvidence` true | H | – |
| oversized_can | normal | `normal_meter_can` (`meter_can_unclear` acceptable) | M | Compact socket. The grey box beside it is a separate enclosure and must be ignored. |
| meter_type | standard | `standardMeter`, `distance_ratio` ~2.1, clean (not clean acceptable) | M | Both can edges and a strip of wall are visible; the meter head is about 40 units tall. |
| meter_mount | standard | `standardMeter`, clean (not clean acceptable) | H | – |
| meter_not_accessible | no | `no` | H | Open lawn in front, eave above 6 ft, no gas in view. The adjacent enclosure counts as building structure. |
| multiple_mains | single main (probable) | `single_main_plus_disconnect_or_subpanel`, false (unclear acceptable) | M | One panel with a top main. An unpictured interior panel can't be excluded. |
| main_panel_type | single main (probable) | `manual_review` (pass acceptable; not scored) | M | The main breaker isn't legible and there's no main-disconnect photo. |
| panel_recall | undeterminable | `manual_review` | H | It looks modern, but the brand is unreadable, so a recalled brand can't be excluded. The missing dead-front in t0009–t0012 is installer work and should be ignored. |
| meter_panel_relationship | co_located_exterior | `co_located_exterior`, `exterior_service_equipment`, subtype `exterior_service_equipment` (`same_wall` or `insufficient_evidence` acceptable) | M | t0003.00, t0012.00. |
| panel_in_house | no | `no` (review acceptable) | M | Clearly outside, on an exterior wall. |
| not_same_wall | no | `no` (review acceptable) | M | Meter and panel are plainly both outside on the same wall. The only residual doubt is whether this is the main panel. |
| panel_not_accessible | no | `no` (review acceptable) | M | Open lawn in front, eave ~8 ft, no gas in view. |
| site | undeterminable | `manual_review` | M | Recall (brand) and meter number are unresolved. Base installed at this house, so a review answer is not a pipeline error. |

Two points for Hunter, both reflected in spec §7.1:
- **The disconnect doesn't argue against C.** C's own reference photo shows a harness ending at a disconnect box.
- **The battery under the window does conflict with C** (spec #32). C prohibits a battery under an operable ground-level window, so Base's actual right-hand position would not pass C as written, unless that window is fixed or "ground-level" means at-grade. The labelled C pass is the spot just left of the window.

A separate scale warning: the bricks look queen or king size, not modular, so the dataset's "3 courses = 8 in" scale reference may be about 20% off. The prompts' own "brick course ~8 in" ruler has the same risk.

## yt-5-M-7f35Tyg (Midwest, winter, 360×480)

- The gas meter and riser are about **1.5 ft left of the electric meter** (t0036.00, t0086.67–t0090.00).
- The electric meter is on the house wall by a corner with a downspout. It's cut off at the frame edge in t0088.67.
- The AC condenser and its disconnect are further left (t0048.67–t0068.00).
- Past the corner are a window (t0092.00), a bump-out over rocks that fails headroom (t0098–t0102), and a deck (t0108+).
- There's possibly a gravel driveway (t0022.00); undeterminable.

| Criterion | Truth | Expected | Conf |
|---|---|---|---|
| placement A / B / C / S | manual_review | `manual_review`; C `needs_more_photos` true (walls to both corners, where the drive is) | H that it's neither a pass nor a reject. Gas is beside the meter, distances can't be resolved, and the wall ends aren't fully seen. |
| meter_not_accessible | manual_review | `manual_review` | H. Gas within ~3 ft is P12's own review trigger. |
| meter_mount | standard | `standardMeter`, clean (not clean acceptable) | M |
| meter_type | standard (best guess) | `standardMeter`, not clean | L (not scored) |
| oversized_can | normal (best guess) | `normal_meter_can` (`meter_can_unclear` acceptable) | L (not scored) |
| meter_number, cl320 | undeterminable | best effort with not clean; `cl320_unclear` | H |
| all panel checks | undeterminable | insufficient / `manual_review` | H |
| site | manual_review | `manual_review` | H |

## yt-0gotY_syWWU (rural Southeast, 640×480)

The probable electric meter is a small round device about 1 ft right of the front door, below a window and about 3.5 ft up (t0033.17). This is unconfirmed (L). There's a ground-level AC condenser and a wall box at the back door (t0076.50, t0077.33), which the dataset notes miss. A gravel drive leads to a detached garage (t0014.00, t0026.50).

Every check is **undeterminable**. Expected: placement `manual_review` under every profile, with C `needs_more_photos` true and no reject; meter reads not clean; panel checks review or insufficient. If the device is the meter, the only candidates are on the street-facing front beside a door with steps, which would still be review under C.

## yt-4h8kyjnPo9s (brick bungalow, 1280×720)

**No electric meter appears in the walk.**
- The gas meter and riser are at the base of the side wall, next to a strip driveway with a parked car (t0095.71, t0096.29).
- A grey box on a brick pier (t0095.71) looks like a telecom box, not a meter.
- Also in view: a small white box (t0099.24) and the fence gate (t0102.18).

Every check is **undeterminable**. Expected: placement `manual_review` with C `needs_more_photos` true (meter location); meter checks not clean; panel checks review. The parked car proves the strip is drivable, so under C the gas and drive clearances would bind on that wall.

## Pending

- Start-range labels (per battery position) for the three laser-GT sites, once a scene with a real or virtual meter exists. PR #13's facade scene is the obvious first one.
- New houses get labelled as they appear in the manifest.
