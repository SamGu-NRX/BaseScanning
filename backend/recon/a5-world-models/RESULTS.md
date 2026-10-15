---
cursor:
  subagentId: "bc-a281bf9c-5fb4-5e98-81c6-eacff867457a"
---

# A5 world models (World Labs Marble 1.1, World API)

## 1. Verdict

**No-go as a measurement backend.** Marble's `metric_scale_factor` was off by −31 % (facade) and +12 % (electro). At declared scale, key-distance errors were 8–19 in median (bar: 2–6 in). On the one real house it **erased the electric meter** and invented a window. A tighter meter-wall capture produced a fantasy scene that none of its own inputs register to. Legitimate role: visualization / "what the site probably looks like" preview, clearly labelled `generated`. It is not a source of pass/fail geometry, and a weak way to suggest next views.

All data real (ETH3D laser GT; one real YouTube house). Credits used: **6,400 of 7,000** (4 worlds × 1,600; PLY export free; no HQ mesh bought). 600 left.

## 2. Inputs and outputs

| World | Inputs (real) | Model | Output | `metric_scale_factor` / ground offset |
|---|---|---|---|---|
| `eth3d-facade` | 8 DSLR frames around the meter wall (DSC_0359–0364, 0420 at 12–15 m; 0396 at 32 m), 2048×1364, `reconstruct_images: true` | marble-1.1 | 1.92 M-Gaussian PLY (1.48 M at opacity ≥ 0.3), SPZ, collider GLB, pano | 2.112 / 1.156 m |
| `eth3d-electro` | 8 DSLR frames facing the meter doors (DSC_9269–9272 at 6–8 m, 9300–9303 at 3–5 m) | marble-1.1 | same | 3.051 / 1.788 m |
| `yt-Qm4b1cw8pV4` (house) | 8 frames t0000–t0012 (driveway corner, then walking along the garage side wall to the meter-main), 854×480 | marble-1.1 | same | 2.563 / 1.510 m |
| `yt-Qm4b1cw8pV4` meter wall only | 8 frames t0007–t0021, all showing the meter wall | marble-1.1 | same | 2.384 / 1.913 m (camera height 1.9 m: implausible for a handheld phone) |

Marble returns **no camera poses**, so I recovered them by render-and-match. I rendered the splat centres with each photo's own intrinsics, matched photo to render with DISK + LightGlue (kornia, CPU), lifted matches with the render depth, then ran PnP-RANSAC and iterated. Failed photos were re-seeded from a registered neighbour. No ground truth was used.

| World | Photos registered | Median reprojection |
|---|---|---|
| Facade | 7 / 8 | 4.4–5.0 px @ 2048 |
| Electro | 3 / 8 | 5.2 px |
| House | 2 / 8 (fx ≈ 700 px, searched) | 3–3.7 px @ 854 |
| Meter-wall world | 0 / 8 | – |

A photo that won't register means the world doesn't reproduce that view.

The shared scorer was run as `score-cloud`, with rotation-aware camera alignment in two modes: `--metric` (rigid, declared scale kept) and similarity (scale fixed from the cameras). For the facade I also ran an oracle scale scan via `--transform`. Scenes with the `generated` flag (§3.1) are in `results/<site>/A5-worldlabs/metric-flagged/` (validated, `generated_regions` asset mask).

## 3. Metrics against the laser

Key distances below are openings, meter, corners and walls; overhead and facing stations are excluded because electro repeats one overhead value 25 times. Completeness is the share of laser samples with a splat point within 10 cm, in the declared frame.

| Run | KDs scored | Median / p90 | ≤ 2 in / ≤ 6 in | Scale error | Meter wall ≤ 10 cm | Wall face ≤ 10 cm | Ground strip ≤ 10 cm |
|---|---|---|---|---|---|---|---|
| Facade, metric (declared scale) | 17 (39 missing, 19 phantom) | 19.4 / 39.1 in | 0 % / 0 % | **−30.8 %** (camera rms 2.2 m) | 36 % | 35 % | 56 % |
| Facade, similarity from cameras | 23 | 21.5 / 30.3 in | 0 % / 9 % | – | 5 % | 6 % | 47 % |
| Facade, oracle scale 1.30 × declared (best of scan 1.20–1.50) | 31 | 3.9 / 18.0 in | 19 % / 74 % | – | 33 % | 34 % | 72 % |
| Electro, metric | 12 (31 missing) | 7.9 / 14.1 in | 17 % / 42 % | **+12.0 %** | 61 % | 51 % | 100 % |
| Electro, similarity from cameras | 12 | 1.1 / 2.6 in | 58 % / 100 % | – | 97 % | 95 % | 100 % |

Reference: COLMAP sparse on the facade, with oracle scale: 0.63 in median (harness README).

Reading:

- **Scale.** The declared metric scale is not trustworthy, and it errs in both directions (−31 %, +12 %). The facade window widths come out at 0.71× their true size. Marble also compresses far cameras: DSC_0396 at 32 m sits inside the world's "bubble" and renders as empty space from its true pose. That makes camera-based similarity worse than declared scale on the facade.
- **Shape where seen closely.** On electro, the door wall 6–8 m from 3 registered cameras is genuinely good after rescaling (door edges and heights within 0.05–3.4 in). So a hybrid (Marble shape plus scale from ARKit poses) is not absurd for a flat, well-seen wall. But the facade needed an oracle scale to reach 3.9 in, and the result is knife-edge: at 1.25× and 1.35× the wall chain collapses (14–27 in median, 6–23 KDs). Adjacent walls are off even at the best scale: w2 is 40 in short, the w3 door 75 in off, corner c1 18 in off with a 13° wrong turn.
- **Close range fails.** Electro views 3–5 m from the wall won't register. From their true poses the world is coarse and shifted, and the real white door with an orange pipe is replaced by a generic grey door. The detail is invented even inside an observed view.

### Invention (unobserved-region geometry)

Scope: splat points inside the GT criteria box (about 29 × 6.5 × 22 m around the meter), with the oracle-scaled / similarity alignment so that scale error doesn't count as invention. Truth uses all 8 input cameras at their true poses against the laser plus occluders. "Hidden" means more than 30 cm behind the visible laser surface. "Disagree" means more than 30 cm from the laser, where the laser has data within 1.5 m.

| | Facade | Electro |
|---|---|---|
| Outside every input frustum | 73 % of points (all ground; 31 % disagree) | 55 % (89 % ground; 51 % disagree, **86 % of the above-ground part disagree**) |
| Inside a frustum but hidden behind real surfaces | 5 % (above ground: 91 % disagree) | 0.2 % |
| Seen | 23 % (above ground: 45 % disagree) | 45 % (above ground: 11 % disagree) |
| Producer `generated` flag (own z-buffer, registered cameras only, no GT) | 80 % flagged | 61 % flagged |
| Flag quality | Flagged 30 % vs unflagged 41 % disagree: **no separation** | Flagged 46 % vs unflagged 2.5 % disagree: **good separation** |

Harness view: 74–76 % of points within 10 m of the meter are unseen by any input camera; `share_flagged_generated` is 0.80 (facade) and 0.53 (electro) in the `metric-flagged` scenes.

The flag only works as well as camera registration does. On the house (2/8 registered) and the meter-wall world (0/8) it cannot be computed meaningfully. Worse, the invention is **not confined to unobserved space**: the house meter sat in the middle of observed frames and was still removed.

### Real house (robustness, no GT)

- **World 3, 8 frames walking to the meter.** Marble's panorama and splat show the meter wall as blank stucco with a small window that doesn't exist. The **meter-main enclosure and conduits are gone**, the open garage is drawn closed with window panels, and a neighbouring house is invented. Only the two driveway-corner frames register; the six meter-wall frames aren't in the geometry at all. There are no meter key distances to score. Camera height (ground offset 1.51 m) is plausible.
- **World 4, 8 frames of the meter wall only.** The world is a hallucinated scene: a big garage door on the meter wall, a breezeway and second garage, and a small box with a coiled wire on a stone pilaster in place of the 36–40 in meter-main. Camera height 1.91 m. 0 / 8 inputs register.

Figures (in `internal/breadth/a5-world-models/figures/`): `house-meter-erased.jpg`, `house-meterwall-world.jpg`, `house-world-ring.jpg`, `house-meterwall-world-ring.jpg`, `facade-splat-from-true-poses.jpg`, `electro-splat-from-true-poses.jpg`, `facade-topdown-metric.png`.

## 4. Runtime, cost, hardware

- **Generation:** 348–381 s per world server-side (the pano step is included), 1,600 credits = **$1.28 per world** at $1 / 1,250 credits. The splat PLY export is free and synchronous. Rate limit is about 3 starts per minute.
- **Our side** (4-core CPU VM, 15 GB, no GPU): pose recovery 3–5.5 min per world (LightGlue on CPU; a GPU would make it about 10× faster); scoring 40–60 s; invention analysis 30 s.
- **Per house:** about $1.28 plus about 12 min wall clock. The limiting factor is not cost; it is that the output can't be trusted.

## 5. Failure modes

1. **Declared metric scale off by −31 % / +12 %.** It carries no uncertainty and the sign is unpredictable. Don't use `metric_scale_factor`.
2. **The meter (the criteria anchor) is deleted or replaced** on a real house, including when it is the subject of every input frame.
3. **Invention inside observed views:** a wrong door on electro, an invented window on the house wall, a closed garage.
4. **Far views compressed and close views coarse.** Registration fails at 3–5 m and at 30 m.
5. **Large invented surroundings.** 55–73 % of points in the criteria box lie outside all input frusta, and above ground they mostly disagree with the laser.
6. **No poses returned.** Coverage and `generated` flags need our own registration, which fails exactly where the world is wrong.
7. **Uneven multi-image integration.** The world is essentially one panorama around one centre, and inputs taken along a walk (the typical capture) are dropped.

## 6. Licence and data handling

- **World Labs API terms:** Paid Account output is commercial-use OK; Free Account output stays non-commercial with World Labs keeping rights, plus an irrevocable training licence. This account's 7,000 credits came with no payment method, so treat these outputs as **Free-account: non-commercial, internal evaluation only**.
- **Model:** closed, hosted only; no weights.
- **Uploads:** 8 ETH3D frames (CC BY-NC-SA) and 16 YouTube frames (uploader's licence, internal use only). One house frame shows a street number. The worlds are private (`public: false`) but stored at World Labs. World ids are in `runs/*/world.json`; deleting them is `DELETE /marble/v1/worlds/{id}`, not done.

## 7. What going deeper would take (not recommended for measurement)

- **Visualization role:** show the Marble world as a labelled "preview" behind the real measured scene. It costs about $1.28 per house and only needs the free PLY export. Every point must be treated as `generated` unless an A1–A3 measured surface is within about 10 cm of it.
- **Next-view suggestion:** the only honest signal is "the producer flag says generated here". That is equivalent to the coverage grid any measured path already builds, so Marble adds nothing.
- **Hybrid shape plus ARKit scale:** it worked on one flat wall (electro, 1.1 in median). To test it properly would need: ARKit-pose rescaling instead of GT cameras; more close-range views (which currently fail); and a check that meters and doors survive (on the house they don't). Given the house result, it is lower value than A1/A3.
- **Not tried:** `marble-1.1-plus` (costs up to 3,100 credits); non-reconstruct multi-image with azimuths (4 images); video input; HQ mesh (3,500 credits; mesh scale undocumented).

## Files

- **Code** (`internal/breadth/a5-world-models/`): `wlclient.py` (API: generate / poll / export, key from `BaseHackathonWorldLabsAPIKey`), `splatpose.py` + `register_inputs.py` (splat loading, render-and-match pose recovery), `invention.py` (seen / hidden / outside-frusta vs laser, producer `generated` flag), `truepose_render.py`, `ring.py`, `topdown.py`.
- **Per-world records:** `runs/<world>/` holds world / operation / export JSON, registration, cameras, invention JSON and transforms. `runs/eth3d-facade/oracle-scale-scan/` holds the scan scores.
- **Scores:** `internal/eval-harness/results/eth3d-{facade,electro}/A5-worldlabs/{metric,shape,metric-flagged}/`.
- **Large assets on the VM only:** `~/work/a5/<world>/splats_full_res.{ply,spz}`, `points_metric.ply`, `collider.glb`, `pano.jpg`.
