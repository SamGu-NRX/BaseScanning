"""Run one depth/geometry model on a list of images and write one <stem>.npz per image plus run.json.

    cd experiments/evals
    uv run --project models python -m models.run --model moge2 \\
        --images images.txt [--intrinsics intrinsics.json] [--max-side N] --out DIR

Each <stem>.npz holds `depth` (float32 HxW metres, z along the optical axis, input resolution, NaN
where invalid), `valid` (bool HxW), `intrinsics` ([fx, fy, cx, cy] OpenCV pixels of the input image,
the ones used or predicted) and, for mapanything, `cam_to_world` (4x4 OpenCV camera, metres, in the
model's shared frame). See README.md in this folder.
"""

from __future__ import annotations

import argparse
import importlib
import json
import platform
import shutil
import sys
import tempfile
import time
from pathlib import Path

from models.common import (
    RunInputs,
    check_intrinsics_fit,
    checkpoint_record,
    depth_summary,
    read_image_list,
    read_intrinsics,
    read_poses,
    require_free_space_for_download,
    set_cache_dirs,
    write_npz,
)

MODULES = {
    "moge2": "models.moge2",
    "da3metric": "models.da3metric",
    "mapanything": "models.map_anything",
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--model", required=True, choices=sorted(MODULES))
    ap.add_argument("--images", required=True, type=Path, help="text file, one image path per line")
    ap.add_argument(
        "--intrinsics",
        type=Path,
        help="JSON: [fx, fy, cx, cy] per image in OpenCV pixels of that image; a list in --images "
        "order or an object keyed by image stem",
    )
    ap.add_argument(
        "--intrinsics-mode",
        choices=["known", "predicted"],
        help="feed --intrinsics to the model (known) or let it predict them (predicted); "
        "default: known when --intrinsics is given",
    )
    ap.add_argument(
        "--poses",
        type=Path,
        help="mapanything only. JSON: 4x4 camera-to-world per image, OpenCV camera, metres",
    )
    ap.add_argument(
        "--max-side",
        type=int,
        help="moge2: downscale the image handed to the model to this longest side; da3metric: "
        "network longest side (default 504); mapanything: network longest side (default: its "
        "518 resolution set)",
    )
    ap.add_argument("--device", default="auto", choices=["auto", "mps", "cpu", "cuda"])
    ap.add_argument(
        "--mps-cap-gb",
        type=float,
        default=3.6,
        help="hard cap on GPU memory after loading, so on the shared Mac a run that needs more "
        "fails instead of growing; MoGe-2 at its default 3600 tokens needs 3.05 GiB",
    )
    ap.add_argument("--fp32", action="store_true", help="disable mixed precision")
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args(argv)

    if args.intrinsics_mode is None:
        args.intrinsics_mode = "known" if args.intrinsics else "predicted"
    if args.intrinsics_mode == "known" and args.intrinsics is None:
        ap.error("--intrinsics-mode known needs --intrinsics")
    if args.model == "da3metric" and args.intrinsics_mode != "known":
        ap.error("da3metric needs --intrinsics: its metres are focal * output / 300")
    if args.poses is not None and args.model != "mapanything":
        ap.error(f"--poses is only accepted by mapanything; {args.model} is single-image")
    if args.max_side is not None and args.max_side < 14:
        ap.error("--max-side must be at least 14")
    return args


def pick_device(requested: str) -> str:
    import torch

    if requested == "auto":
        if torch.cuda.is_available():
            return "cuda"
        return "mps" if torch.backends.mps.is_available() else "cpu"
    if requested == "mps" and not torch.backends.mps.is_available():
        raise RuntimeError("--device mps requested but torch reports MPS unavailable")
    if requested == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("--device cuda requested but torch reports CUDA unavailable")
    return requested


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    caches = set_cache_dirs()

    images = read_image_list(args.images)
    intrinsics = read_intrinsics(args.intrinsics, images) if args.intrinsics else None
    poses = read_poses(args.poses, images) if args.poses else None
    if intrinsics is not None:
        from PIL import Image

        for p, k in zip(images, intrinsics, strict=True):
            with Image.open(p) as im:  # reads the header only
                w, h = im.size
            check_intrinsics_fit(k, w, h, p.name)

    import torch

    module = importlib.import_module(MODULES[args.model])
    device = pick_device(args.device)
    require_free_space_for_download(module.REPO, module.FILENAME, module.REVISION)
    # Everything is written to a staging folder and swapped in only when the whole run succeeds,
    # so a run that stops midway (out of memory, cancelled) leaves the previous run whole instead of
    # a mix of its depth maps and new ones under the old run.json.
    args.out.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=args.out.name + ".staging-", dir=args.out.parent))
    try:
        run_into(args, stage, images, intrinsics, poses, caches, module, device, torch)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    publish(stage, args.out)
    print(f"wrote the run's npz files and run.json to {args.out}", file=sys.stderr)


def publish(stage: Path, out: Path) -> None:
    """Publish a complete run, preserving the old directory if its replacement fails."""
    previous = out.with_name(out.name + ".previous")
    if previous.exists():
        raise RuntimeError(f"refusing to modify existing backup directory {previous}")
    if out.exists():
        out.rename(previous)
    try:
        stage.rename(out)
    except BaseException:
        if previous.exists() and not out.exists():
            previous.rename(out)
        raise
    shutil.rmtree(previous, ignore_errors=True)


def run_into(args, out: Path, images, intrinsics, poses, caches, module, device, torch) -> None:
    """Load the model, verify its checkpoint, and write every prediction and run.json to `out`."""
    start = time.perf_counter()
    model = module.load(device)
    load_seconds = time.perf_counter() - start
    # Before any output: a replaced cached checkpoint must not leave depth maps behind.
    checkpoint = checkpoint_record(module.REPO, module.FILENAME, module.REVISION, module.SHA256)
    if device == "mps":
        torch.mps.empty_cache()
        torch.mps.set_per_process_memory_fraction(
            args.mps_cap_gb * 1e9 / torch.mps.recommended_max_memory()
        )
    inputs = RunInputs(
        images=images,
        intrinsics=intrinsics,
        intrinsics_mode=args.intrinsics_mode,
        poses=poses,
        max_side=args.max_side,
        device=device,
        fp32=args.fp32,
    )
    # Each result is written as soon as it exists: MoGe-2 and DA3 yield one image at a time, so a
    # long session never holds every depth map in memory at once.
    per_image = []
    for res in module.run(model, inputs):
        write_npz(
            out,
            res.path.stem,
            res.depth,
            res.valid,
            res.intrinsics,
            res.cam_to_world,
            res.arrays,
        )
        summary = depth_summary(res.depth, res.valid)
        per_image.append(
            {
                "image": str(res.path),
                "stem": res.path.stem,
                "seconds": round(res.seconds, 3),
                "network_input_wh": list(res.network_wh),
                "resample": res.resample.to_json(),
                "intrinsics": [round(float(v), 4) for v in res.intrinsics],
                **(
                    {"cam_to_world": res.cam_to_world.tolist()}
                    if res.cam_to_world is not None
                    else {}
                ),
                "depth": summary,
                **res.extra,
            }
        )
        print(f"{res.path.stem}: {res.seconds:.2f} s, {json.dumps(summary)}", file=sys.stderr)

    del model
    if device == "mps":
        torch.mps.empty_cache()

    run = {
        "model": args.model,
        "license": module.LICENSE,
        "checkpoint": checkpoint,
        "code": module.CODE,
        "device": device,
        "torch": torch.__version__,
        "platform": platform.platform(),
        "mixed_precision": not args.fp32,
        "intrinsics_mode": args.intrinsics_mode,
        "intrinsics_file": str(args.intrinsics) if args.intrinsics else None,
        "poses_used": poses is not None,
        "poses_file": str(args.poses) if args.poses else None,
        "max_side": args.max_side,
        "load_seconds": round(load_seconds, 2),
        "depth_upsampling": "bilinear; a pixel is valid only if every network pixel it draws on is valid",
        "caches": caches,
        "images": per_image,
    }
    if args.model == "mapanything":
        run["timing_note"] = "views run jointly; per-image seconds = joint seconds / views"
    (out / "run.json").write_text(json.dumps(run, indent=2) + "\n")


if __name__ == "__main__":
    main()
