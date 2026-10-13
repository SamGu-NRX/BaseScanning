# Depth and geometry model runners

Runs three commercially licensed models on a list of photos and writes metric depth in one format, so the wall-reconstruction eval (question 2 in `../README.md`) can score each against ETH3D's laser scans.

## Install

A separate uv project, because the models pull in torch and pinned git packages the base eval does not need.

```sh
cd experiments/evals
uv sync --project models
uv run --project models pytest -q models/tests
```

Checkpoints download on first use into `$HOUSE_SCANNING_DATA/evals/hf-cache` (`HF_HOME`) and `evals/torch-cache` (`TORCH_HOME`) unless those variables are already set. A run refuses to start a download with less than 6 GB free on the volume holding `HF_HOME`, and checks each checkpoint's sha256 against the pinned value. The grouped MapAnything runner (`models.run_groups`) does both too, records the checkpoint in each group's `run.json`, and reuses a group's output only when a fingerprint of its members, image bytes, intrinsics, poses, input size and checkpoint matches.

## Run

```sh
cd experiments/evals
uv run --project models python -m models.run --model moge2       --images images.txt [--intrinsics k.json] --out DIR
uv run --project models python -m models.run --model da3metric   --images images.txt  --intrinsics k.json  --out DIR
uv run --project models python -m models.run --model mapanything --images images.txt [--intrinsics k.json] [--poses poses.json] --out DIR
```

- `--images`: one image path per line. Stems must be unique.
- `--intrinsics`: `[fx, fy, cx, cy]` per image in OpenCV pixels of that image (pixel centres at integers). Either a list in `--images` order or an object keyed by image path or stem. COLMAP and ETH3D principal points put the first pixel centre at 0.5, so subtract 0.5 from them.
- `--intrinsics-mode known|predicted`: defaults to `known` when `--intrinsics` is given.
- `--poses` (mapanything only): 4x4 camera-to-world per image, OpenCV camera (+x right, +y down, +z forward), metres.
- `--max-side N`: see the per-model notes. `--device auto|mps|cpu|cuda`, `--fp32` turns off mixed precision.

Each run loads one model and processes images one at a time; mapanything runs all views in one joint call.

## Output

`DIR/<stem>.npz`:

| key | contents |
| --- | --- |
| `depth` | float32 HxW, metres, z along the optical axis, at the input image's resolution; NaN where invalid |
| `valid` | bool HxW |
| `intrinsics` | float64 `[fx, fy, cx, cy]`, OpenCV pixels of the input image: the known ones in `known` mode, the model's otherwise |
| `cam_to_world` | mapanything only. float64 4x4, OpenCV camera, metres. Frame: the first image's camera, or the `--poses` world when poses are given |
| `intrinsics_predicted` | mapanything only. What its output rays imply, in input pixels |

A run writes into `DIR.staging` and replaces `DIR` only when every image is done, so a run that fails midway leaves the previous run whole. `DIR/run.json` records the model, checkpoint repo, revision and sha256, code commit, device, torch version, flags, load time and, per image, seconds, the network input size, the exact resize and crop, and a depth summary.

Network outputs are resampled to the input grid bilinearly. A pixel is valid only if every network pixel it draws on is valid and it lies inside the region the network saw; pixels removed by a crop are invalid.

## Models

| `--model` | Checkpoint (revision) | License | Code (commit) |
| --- | --- | --- | --- |
| `moge2` | [`Ruicheng/moge-2-vitl-normal`](https://huggingface.co/Ruicheng/moge-2-vitl-normal) (`cb0e8bb`) | weights unverified; code MIT | microsoft/MoGe `0744441` |
| `da3metric` | [`depth-anything/DA3METRIC-LARGE`](https://huggingface.co/depth-anything/DA3METRIC-LARGE) (`4010e39`) | Apache-2.0 | ByteDance-Seed/Depth-Anything-3 `3d835ec` |
| `mapanything` | [`facebook/map-anything-apache`](https://huggingface.co/facebook/map-anything-apache) (`00f9c24`) | Apache-2.0 | facebookresearch/map-anything `3d10cf7` |

`facebook/map-anything` (without `-apache`) is CC-BY-NC and is not used. Full hashes are in `pyproject.toml` and each runner module.

`pyproject.toml` replaces the three packages' requirement lists (`tool.uv.dependency-metadata`) with what inference imports. As published they require gradio, open3d, pycolmap, xformers, evo, moviepy and `numpy<2`, which the inference path never imports.

### How each gets metres

- **MoGe-2** outputs metric depth directly. `MoGeModel.infer` multiplies its affine point map by the predicted `metric_scale` (`moge/model/v2.py:279-283` at `0744441`) and resizes outputs to the image it was given (`v2.py:170`). Its one camera input is `fov_x`. In `known` mode the runner passes `fov_x = 2 atan(W / 2 fx)`; MoGe then fixes the focal and solves only the depth shift (`v2.py:259-264`), assuming square pixels and a centred principal point, so the saved intrinsics are MoGe's (fy = fx, centre). It runs at a fixed 3600-token budget (1120x630 for 16:9) whatever the input size. `--max-side` only shrinks the image handed to it; use it for full-size 24 MP photos.
- **DA3 metric** outputs depth for a canonical 300 px focal. The rule is `metric_depth = focal * net_output / 300` with `focal` the mean of fx and fy in pixels (`README.md:235` at `3d835ec`; `apply_metric_scaling`, `src/depth_anything_3/utils/alignment.py:118-133`). The package applies it with the intrinsics of the resized network image (`model/da3.py:379`), so the runner uses the known focal scaled to the network size. With no known focal there is no metric depth, so `da3metric` refuses to run without `--intrinsics`. Preprocessing is the package's `InputProcessor` (`upper_bound_resize`, longest side 504 or `--max-side`, sides rounded to multiples of 14, no crop). Sky is invalid: the model treats `sky < 0.3` as non-sky (`model/da3.py:161`) and fills sky with a placeholder depth.
- **MapAnything** outputs metric geometry directly: it predicts and applies a metric scale factor (reported per image as `metric_scaling_factor` in run.json), and `depth_z` is z-depth. Every view shares one network size: the package's choice for the mean aspect ratio from its 518 resolution set (518x294 for 16:9, 518x336 for 3:2), or longest side `--max-side`. The runner scales each image to cover that size and centre-crops it itself, so the crop is known exactly. Output poses are relative to the first view's camera even when `--poses` are given; the runner multiplies them by the first input pose to put them back in the input frame.

## Smoke test on real images

Five consecutive keyframes (`k00020` to `k00024`, about 0.5 m apart) of `~/house-scanning-data/replays/advio-20-0040-0075`, rotated upright because the replay JPEGs are sideways sensor images (sky on the left):

```sh
uv run --project models python -m models.smoke_advio prepare --ids k00020 k00021 k00022 k00023 k00024
# then models.run for each model with --images/--intrinsics/--poses from ~/house-scanning-data/evals/model-smoke/advio-input
uv run --project models python -m models.smoke_advio summarize ~/house-scanning-data/evals/model-smoke/<run> ...
```

"Ground" is the median valid depth in the bottom quarter of the upright image, the road just ahead. For a phone about 1.4 m up (assumed; ADVIO gives no height) and pitched 5 to 10° down (from the ARKit poses), the middle row of that band meets the road at a z-depth of about 2.3 to 2.7 m. The models report 2.4 to 3.9 m, with DA3 the farthest.

| Run | Ground median per image (m) | Seconds per image | Network input (W x H) |
| --- | --- | --- | --- |
| moge2, known intrinsics | 3.49, 3.22, 2.90, 2.93, 2.97 | 0.95 | 630x1120 |
| moge2, predicted intrinsics (fx 1011 to 1093, true 1081) | 3.39, 3.26, 2.70, 2.96, 3.00 | 0.95 | 630x1120 |
| da3metric | 3.93, 3.51, 3.25, 3.44, 3.35 | 0.17 to 0.19 | 280x504 |
| mapanything, images only (fx about 1268) | 3.36, 3.26, 3.21, 2.90, 2.87 | 0.72 | 294x518 |
| mapanything, known intrinsics | 3.09, 3.00, 2.93, 2.66, 2.64 | 0.6 to 1.5 | 294x518 |
| mapanything, known intrinsics and ARKit poses | 2.68, 2.58, 2.44, 2.35, 2.37 | 0.7 to 0.9 | 294x518 |

All ran on MPS (M4 Pro, torch 2.14) with mixed precision. The first image of a run took 0.1 to 2.6 s longer while kernels warmed up. Model load takes 2 to 6 s (MoGe, DA3) and 26 to 42 s (MapAnything). Timings varied up to 2x between repeats because other jobs shared the machine. None of these were measured on the ETH3D images.

MapAnything camera baseline, k00020 to k00024, against ARKit's 2.019 m (consecutive ARKit steps 0.50 to 0.51 m):

| Inputs | Predicted first-to-last (m) | Ratio | Consecutive steps (m) |
| --- | --- | --- | --- |
| images only | 2.511 | 1.244 | 0.55, 0.60, 0.80, 0.61 |
| + known intrinsics | 2.438 | 1.208 | 0.54, 0.57, 0.83, 0.59 |
| + known intrinsics and poses | 2.041 | 1.011 | 0.50, 0.53, 0.51, 0.51 |

With poses given, the output poses reproduce the input ones to within 2.3 cm and 0.11°, which confirms the frame conversion; that row is not an independent measurement.

## Memory

The Mac these run on is shared, so each process should stay near 4 GB. MoGe-2 at its default 3600 tokens needs 3.05 GiB of GPU memory and peaks at 4.07 GB in total, flat over a session, because `run.py` writes each depth map as soon as it exists; `--mps-cap-gb` (3.6 by default) makes a run that needs more fail instead of growing. Depth Anything 3's peak was not measured; its checkpoint is 1.3 GB. MapAnything's `from_pretrained` builds the 1.2 B-parameter model in fp32 (4.9 GB) and then reads the 4.9 GB fp32 checkpoint into it. `map_anything.load` instead builds the model on the meta device and streams each tensor straight to the GPU, casting the two transformer stacks (92% of the weights) to bf16: 2.64 GB of weights. `run_groups.py` caps GPU memory at 3.8 GB (`--mps-cap-gb`). Measured peak footprint: 4.05 GB for 2- and 4-view groups at 392 px. Its native 518 px, or 8 views, runs out of memory under that cap.

## Known issues

- MoGe's current `main` (MoGe-3, `74fbce0`) limits its uv environments to Linux and Windows, so the pin is the last MoGe-2 commit.
- On MPS, MoGe warns `MPS Autocast only supports dtypes of torch.bfloat16, torch.float16 currently` for its fp32 post-processing block. The block casts to float32 itself, so the warning is harmless.
- MapAnything's first run failed with `TypeError: Cannot convert a MPS Tensor to float64 dtype as the MPS framework doesn't support float64` when converting its outputs; the runner now moves tensors to the CPU before converting.
- Loading MapAnything fetches DINOv2 model code from `facebookresearch/dinov2` `main` through torch hub (uniception's encoder; code only, about 4.5 MB, no weights). That fetch is not pinned.
