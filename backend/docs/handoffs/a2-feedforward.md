---
cursor:
  subagentId: "bc-901df09a-a67a-5f2b-b517-cda7466f7eab"
---

# A2 feed-forward multi-view models: builder handoff notes

## 1. One-paragraph summary
Path A2 measured feed-forward multi-view models on real ETH3D images against the laser scans (facade, electro, delivery_area). ARKit-like poses are synthetic, from the packet generator.

- **Production keeps:** π³ (Pi3, BSD-2) for dense-geometry windows. DA3-Large 1.1 (Apache-2.0) is the pose-conditioned alternative.
- **Dropped:** MapAnything (metric scale off by −16% to −36%), VGGT / MASt3R / DA3-Nested (non-commercial).
- **Headline:** π³ on 16 facade meter-wall frames, scaled by exact poses, gets 0.8 / 2.9 in (median / p90) on observed key distances through the shared `evalharness score-cloud`. On 8-view ETH3D groups, 1–3 m pairs: 0.6 / 2.9 in (delivery_area) and 0.9 / 2.1 in (electro).
- **Verdict: conditional go.** Shape meets the 2–6 in bar. Scale from ARKit poses does not: the LiDAR profile's session scale runs −1.8% to +4.4%, non-LiDAR −0.4% to −17.9%, and it passes 1:1 into every length. With one known reference distance, π³ gets 2.0–2.9 in p90.
- **Report:** `internal/breadth/A2-feedforward.md`. **Code:** `internal/breadth/a2-feedforward-code/` (store only, not in any repo).

## 2. Environment that worked
- **VM:** 4-core CPU with AMX-BF16, 15 GB RAM, of which only ~7 GB is usable (~8 GB is taken by the VM itself). No GPU. Ubuntu, Python 3.12.3.
- **Code layout** (hard-coded as `ROOT = ~/a2` in `a2ff/common.py`): code `~/a2/code`, venv `~/a2/venv`, data `~/a2/data/eth3d`, symlink `~/a2/hsdata/evals/eth3d -> ~/a2/data/eth3d`, predictions `~/a2/preds`, HF cache `~/a2/hf`, torch hub `~/a2/torch-cache`.
- **Install** (the whole block is `a2-feedforward-code/scripts/setup_a2.sh`; ~2 min):
```
pip3 install --user --break-system-packages uv py7zr && export PATH=$HOME/.local/bin:$PATH
cd ~/a2 && uv venv --python 3.12 venv && source venv/bin/activate
uv pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu     # got torch 2.14.0+cpu
uv pip install "numpy<2" opencv-python-headless scipy einops safetensors huggingface_hub pillow trimesh plyfile \
  omegaconf hydra-core python-box natsort orjson pillow-heif requests tqdm "uniception==0.1.7" e3nn imageio pycolmap \
  py7zr typer addict "moviepy==1.0.3" evo shapely jsonschema pydantic pyyaml
git clone https://github.com/yyfz/Pi3 ~/a2/repos/Pi3 && git -C ~/a2/repos/Pi3 checkout 9fa3ddb
git clone https://github.com/ByteDance-Seed/Depth-Anything-3 ~/a2/repos/Depth-Anything-3 && git -C ~/a2/repos/Depth-Anything-3 checkout 3d835ec
uv pip install --no-deps -e ~/a2/repos/Pi3 -e ~/a2/repos/Depth-Anything-3
```
- **Pins:**
  - `numpy<2` (got 1.26.4) and `uniception==0.1.7` (for MapAnything, now dropped).
  - `pycolmap` 4.2.0 matches the harness requirement.
  - Use `--no-deps` for the repo installs: their own requirements pull CUDA torch.
- **Weights** (`a2-feedforward-code/weights.py`, `HF_HOME=~/a2/hf`, ~25 GB for all seven; the needed ones are ~3 GB and downloaded in under a minute):
  - `yyfz233/Pi3` @ `ae722e7039287d0c8fde9f11f197f804f44b510c`: BSD-2. Pi3X is NC; don't use it.
  - `depth-anything/DA3-LARGE-1.1`: Apache-2.0. `DA3METRIC-LARGE`: Apache-2.0 (single-image metric, not used multi-view).
  - Dropped: `facebook/map-anything-apache` @ `00f9c245…` (Apache), `facebook/VGGT-1B` @ `860abec…` (CC BY-NC; `VGGT-1B-Commercial` is gated), `DA3NESTED-GIANT-LARGE-1.1` (CC BY-NC), `naver/MASt3R_…_metric` (CC BY-NC-SA).
- **ETH3D:** `bash a2-feedforward-code/dl.sh`, which fetches `https://www.eth3d.net/data/{facade,electro,delivery_area}_dslr_{undistorted,scan_eval,occlusion}.7z` (3.0 GB, 4.6 GB extracted, ~5 min). Checksums are in `eth3d_sha256.txt`. Licence CC BY-NC-SA: evaluation only.
- **No install failures.** One warning: π³ prints "cannot find cuda-compiled version of RoPE2D, using a slow pytorch version"; this is harmless on CPU.

## 3. Reproduce the headline result
```
cp -r <store>/internal/breadth/a2-feedforward-code ~/a2/code && cd ~/a2/code && source ~/a2/venv/bin/activate
for s in delivery_area electro facade; do python -m a2ff.prepare $s; done
python -m a2ff.run --model pi3 --scene delivery_area electro facade --sizes 8 --seeds 8 --modes images
python -m a2ff.run --model da3large --scene delivery_area electro facade --sizes 8 --seeds 8 --modes images KT:exact KT:nonlidar_modern.draw0
python -m a2ff.score --scene delivery_area electro facade --tag phase1          # -> ~/a2/results/phase1.json/.md
python -m a2ff.report_tables phase1                                              # -> tables_phase1.md
cd ~/a2 && mkdir -p harness && cp -r <store>/internal/eval-harness/{evalharness,eth3d,schemas,vendor} harness/
cd ~/a2/harness && PYTHONPATH=~/a2/code python -m a2ff.peak eth3d.build_gt --data ~/a2/data/eth3d/facade --out ~/a2/harness-gt/facade-gt
cd ~/a2/code && python -m a2ff.harness_run --model pi3 --modes images && python -m a2ff.harness_summary
```
- **Runtime per step:**
  - `prepare`: 2–3 min per scene, peak 3.4 GB. It regenerates byte-identical poses and subsets (fixed seeds); verify with `cmp` against `outputs/<scene>/arkit_poses/`.
  - π³: 9–13 s per 8-view group. DA3-L: 8.7 s per group.
  - `score`: ~7 min for 3 scenes, peak 1.9 GB.
  - `build_gt`: 80 s.
  - `harness_run`: ~75 s per 16-view run, then ~30–70 s per scored variant.
- **Expected output**, `phase1.md`, delivery_area, within 6 m, surface interior:
  - `pi3_518_images | posthoc:exact | 8 | 8 | 0.6 / 2.9 | 1.2 / 5.4 | +0.8% | +0.6% | 90.3 / 97.5 | 0.6 / 2.0 | 1.2 / 4.0`
  - `harness summary`: `pi3_518_images | posthoc-exact (rot+centre aligned) | 47/2/3 | 1.0 / 8.8 (n=38..45) | 82% | 0.8 / 2.9 (n=31)`
- **Stored results to diff against:** `outputs/scores/phase1.json`, `outputs/shared-scorer/summary.md` and `geomscores.tgz`.

## 4. Parameters that matter
- **Network size.** 518×336 (π³) cover-crops ~9 rows off the top and bottom of 3:2 images. That dropped facade near-ground completeness to 0.50 (π³ has no depth there). Use an aspect-matched size: 504×336 (DA3 default, no crop) or 518×350 (`pi3tall` adapter, untested). Sides must be multiples of 14.
- **Views per window:** only 8 and 16 were tested. 8 views are enough for 0.6–0.9 in medians. 16 views fit in 7 GB for π³ and DA3.
- **Precision:** trunk in bf16 autocast with 2-D Linear weights stored in bf16, streamed onto a meta-initialised model (`run.py stream_load`); heads in fp32. This keeps π³ at ~5 GB. Loading the whole model in fp32 is fine for π³ and DA3-L, but DA3-Nested (6.8 GB) OOMs.
- **DA3 on CPU:** monkeypatch `torch.cuda.is_bf16_supported = lambda *a, **k: True`, or DA3 autocasts to fp16 on CPU. `process_res=max(net)`, `process_res_method="upper_bound_resize"`; the adapter raises if DA3 changes the size.
- **Post-hoc scale:** Umeyama Sim(3) of the window's model camera centres to the ARKit centres (`umeyama_sim3`). Window cameras only 1–3 m apart amplify per-frame jitter.
- **Unmasked output:** all finite depths are kept. π³ `conf > 0.1` is saved as `model_mask` but barely changes scores.
- **π³ intrinsics:** a least-squares fx/fy fit from its local point map, with the principal point fixed at the image centre (`fit_intrinsics_from_local_points`).
- **Coverage rule** (`score.py`): `MIN_VIEWS=2`, `MAX_INCIDENCE_DEG=75` (contract §3.1). Don't change; it matches the harness.
- **Harness export:** 60 000 random finite-depth points per view (`POINTS_PER_VIEW`); a depth-edge filter is not implemented.

## 5. Dead ends
- **MapAnything:**
  - images-only metric scale is −30% (delivery_area), −18% (electro), −23% to −36% (facade);
  - `K:exact` changes nothing;
  - `KT:exact` still gives −7.7% / +0.4% / −12.8% to −22.8%;
  - it is 4× slower (34 s per 8 views) and its non-ambiguous mask removed nothing.
  - Don't revisit.
- **Trusting any model's own "metric" output:** π³ and DA3-L 1.1 raw outputs are normalised (−82% to −94%). Scale must come from outside.
- **Harness default alignment (`score-cloud` without `--transform`):** it aligns by camera centres only. With near-collinear cameras (two clusters ~30 m apart) roll is free and the cloud tilts: π³ + LiDAR scored 6 of 50 key distances. Fix: `harness_run.pose_alignment` (Procrustes on camera rotations plus centres, passed via `--transform`) gives 42 of 50.
- **Waiting with `pgrep -f "<pattern>"` in a bash loop** matches its own command line and loops forever; wait on PIDs or flag files instead.
- **Editing a bash queue script while it runs** corrupts it (bash reads the script by offset); kill the queue and start a new one.
- **Running model jobs in parallel:** ~8 GB peak RSS for MapAnything and VGGT; run strictly one model process at a time. Scoring (≤2 GB) can overlap with π³ or DA3.

## 6. Data quirks
- **ETH3D group ids** are `n<size>-<seed view>`; the 8 seeds per scene are `np.linspace` over the views sorted by name. Groups of ≤8 views are identical to PR #12's.
- **Two frames:** ARKit-like pose files are in a gravity-aligned ARKit world (y-up, OpenCV camera axes); `exact.json` is in the ETH3D laser frame. Pair distances are frame-invariant, so both score the same way.
- **Facade:** the photographer stood ~10 m back, so "within 6 m" contains only ground points at the bottom image edge. Use "all points" for facade. The walk is 161 m, so later seeds carry more simulated drift.
- **Only 12–16 of the 76 facade frames see the meter-wall elements:** DSC_0357–0366, 0394, 0395, 0419–0422. The meter is invented (PR #13), at wall s = 0, 1.524 m high.
- **Laser GT for the harness** is built per VM (`eth3d/build_gt.py`, 25 MB PLY, not in the store).
- **Store I/O is slow:** copying 3 MB of small files took ~6 min, and one earlier write hit a transient `Input/output error` (retry succeeded). Verify copies with `cmp`.

## 7. Integration notes for the pipeline
- **Inputs:**
  - keyframe RGB (any aspect; resize to an aspect-matched multiple of 14, longest side 504–518);
  - per-frame ARKit camera-to-world poses and intrinsics (`keyframes[]` in `docs/scene-and-verdict-format.md` §2.9);
  - the window membership: 8–16 temporally or spatially adjacent frames that look the same way (PR #12's rule is forward·forward > 0.5, nearest centres);
  - a scale source that is not the trajectory (LiDAR depth or a known reference), when present.
- **Per window:** run π³ images-only, then Sim(3)-fit the model cameras to the ARKit cameras.
  - If a scale source exists, use it for `s` and use the ARKit poses only for R and t.
  - Emit per-frame depth (metres, float32, NaN = no prediction), confidence, K, and camera-to-world in the scene frame.
  - Then fuse into a cloud, with `generated_regions[]` left empty: π³ predicts only pixels that were seen.
- **Coverage:** use `evalharness.coverage.query_region` on the fused scene for every measurement (§3.1). A region seen by < 2 views at ≤ 75° is `unobserved` / `grazing` → unsure.
- **Error codes to surface:**
  - `window_pose_inconsistent`: the Sim(3) camera residual is large; this catches the long-tail windows with p90 ≥ 23 in.
  - `scale_from_trajectory_only`: attach ±(ARKit scale error) to every length (≥ 2–5% non-LiDAR).
  - `edge_geometry_unreliable`: depth-edge pixels.
- **Degrade gracefully:** drop failed windows rather than the house; with no scale source, emit shape-only geometry plus a scale ± and let the rules return unsure.

## 8. Open risks
- **Untested on real iPhone captures:** all poses are simulated drift on DSLR images. Cheapest check: one real ARKit capture of an ETH3D-like wall with a taped reference, scored for pair lengths.
- **Whole-house global consistency:** only local windows were scored; the loop closure of chained π³ windows is unknown. Cheapest check: chain facade windows over all 76 frames through ARKit poses and run the shared scorer on the fused cloud.
- **Walkaround video (motion blur, 360–720 px, portrait):** `a2ff/house.py` exists, but only a 6-frame smoke run was done (fragmented; 21° ground-plane tilt). Cheapest check: run `python -m a2ff.house --model pi3 --house yt-0gotY_syWWU yt-4h8kyjnPo9s yt-5-M-7f35Tyg --frames 16` (~2 min).
- **Edges and near ground:** 1–3 m edge pairs have p90 5–18 in (π³), which fails the opening-clearance precision bar. Needs a confidence or depth-edge filter plus per-edge ±.
- **Scale via LiDAR depth maps** is untested; this is the biggest lever. Simulate it with the packet generator's depth noise, per window.
- **Aspect-matched π³ (518×350)** is untested.

## 9. What you would do differently
- Use aspect-matched network sizes from the start; the crop artifact cost a facade row of results.
- Wire the shared scorer with rotation-aware alignment before running the grid, and export confidence-filtered clouds (fewer phantom key distances).
- Run houses and whole-house chaining before extra metadata profiles; profile draws only re-measure the ARKit scale error.
- Measure scale options (LiDAR depth, reference object) in the same grid as the models; scale, not shape, decides the verdict.
- Keep one model process at a time and a single queue driven by flag files.
