"""Run MapAnything once per view group of an ETH3D scene, loading the model once.

    cd experiments/evals
    uv run --project models python -m models.run_groups --scene-dir ~/house-scanning-data/evals/eth3d/facade \\
        --out ~/house-scanning-data/evals/predictions/facade/mapanything

Groups come from the scene's `subsets.json` (written by `python -m evals.recon prepare`). Each group
of n views seeded by view S is written to `<out>/n<n>-<S>/<stem>.npz`, the layout `evals.recon`
reads, plus a `run.json` with timing and the model's metric scale factor. Known intrinsics are
passed (a phone knows its own). With `--poses-file` (written by `python -m evals.pose_priors
prepare`) each group also gets its AR-like camera poses as a metric prior, and the outputs are
placed in those poses' frame; without it every group's frame and scale are the model's own.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
from pathlib import Path

import numpy as np

from models import map_anything
from models.common import (
    RunInputs,
    checkpoint_record,
    fingerprint,
    read_intrinsics,
    require_free_space_for_download,
    set_cache_dirs,
    write_npz,
)
from models.map_anything import load, run
from models.run import pick_device, publish


def write_group(out: Path, results, summary: dict) -> None:
    """Write one group's depth maps and run.json, all or nothing: they go to a staging folder
    that `publish` swaps in for `out` only once every file is written, so a failure partway
    leaves the previous outputs and run.json as they were, never a mix of old and new views."""
    stage = out.with_name(out.name + ".staging")
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True)
    try:
        for res in results:
            write_npz(
                stage,
                res.path.stem,
                res.depth,
                res.valid,
                res.intrinsics,
                res.cam_to_world,
                res.arrays,
            )
        # What reuse validates: each published NPZ's content identity, so a deleted, truncated
        # or substituted output never rides on run.json's input fingerprint alone.
        summary["outputs"] = {
            p.name.removesuffix(".npz"): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(stage.glob("*.npz"))
        }
        (stage / "run.json").write_text(json.dumps(summary, indent=1) + "\n")
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    publish(stage, out)


def reusable(out: Path, members: list[str], key: str) -> bool:
    """A cache hit needs more than run.json naming this run's inputs: every member's NPZ must
    exist and still hash to what the run recorded. A deleted, truncated or substituted output
    regenerates instead of failing later on np.load or scoring substituted geometry."""
    try:
        summary = json.loads((out / "run.json").read_text())
    except (OSError, json.JSONDecodeError):
        return False
    if summary.get("fingerprint") != key:
        return False
    recorded = summary.get("outputs")
    if not isinstance(recorded, dict) or set(recorded) != set(members):
        return False
    for stem, digest in recorded.items():
        try:
            if hashlib.sha256((out / f"{stem}.npz").read_bytes()).hexdigest() != digest:
                return False
        except OSError:
            return False
    return True


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--scene-dir", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--poses-file", type=Path, help="JSON {group id: {poses: {view: 4x4}}}")
    ap.add_argument("--sizes", nargs="+", default=None, help="only these group sizes")
    ap.add_argument(
        "--max-side",
        type=int,
        default=None,
        help="network longest side (default: MapAnything's 518)",
    )
    ap.add_argument("--device", default="auto", choices=["auto", "mps", "cpu"])
    ap.add_argument(
        "--mps-cap-gb",
        type=float,
        default=3.8,
        help="hard cap on GPU memory, so on the shared Mac a run that needs more fails instead of "
        "growing: the weights take 2.64 GB, and 2- and 4-view groups at 392 px fit under 3.8",
    )
    args = ap.parse_args()
    set_cache_dirs()
    import torch

    groups = json.loads((args.scene_dir / "subsets.json").read_text())
    if args.sizes:
        groups = {n: g for n, g in groups.items() if n in args.sizes}
    pose_sets = json.loads(args.poses_file.read_text()) if args.poses_file else None
    images_dir = args.scene_dir / "images_1024"
    intr_file = args.scene_dir / "model_inputs" / "intrinsics.json"
    device = pick_device(args.device)
    ckpt = (map_anything.REPO, map_anything.FILENAME, map_anything.REVISION)
    require_free_space_for_download(*ckpt)
    t0 = time.perf_counter()
    model = load(device)
    checkpoint = checkpoint_record(*ckpt, map_anything.SHA256)
    if device == "mps":
        torch.mps.empty_cache()  # loading leaves about 0.6 GB of freed blocks cached
        torch.mps.set_per_process_memory_fraction(
            args.mps_cap_gb * 1e9 / torch.mps.recommended_max_memory()
        )
    print(f"loaded in {time.perf_counter() - t0:.1f} s on {device}", file=sys.stderr)
    for n, members_list in groups.items():
        for members in members_list:
            gid = f"n{n}-{members[0]}"
            out = args.out / gid
            poses = None
            if pose_sets is not None:
                poses = [np.array(pose_sets[gid]["poses"][m]) for m in members]
            images = [images_dir / f"{m}.jpg" for m in members]
            intrinsics = read_intrinsics(intr_file, images)
            key = fingerprint(
                members, images, intrinsics, poses, args.max_side, checkpoint["sha256"]
            )
            # Reuse only an output made from these exact inputs and this checkpoint, whose
            # every member NPZ is still the file the run recorded.
            if reusable(out, members, key):
                continue
            inputs = RunInputs(
                images=images,
                intrinsics=intrinsics,
                intrinsics_mode="known",
                poses=poses,
                max_side=args.max_side,
                device=device,
                fp32=False,
            )
            results = run(model, inputs)
            summary = {
                "fingerprint": key,
                "checkpoint": checkpoint,
                "members": members,
                "seconds_per_view": round(results[0].seconds, 3),
                "network_wh": list(results[0].network_wh),
                "weights": "transformer stacks bf16, rest fp32 (models/map_anything.py load)",
                "metric_scaling_factor": [r.extra["metric_scaling_factor"] for r in results],
                "poses_file": str(args.poses_file) if args.poses_file else None,
                # What evals.pose_priors labels the row by: the exact pose file this run used.
                "poses_sha256": (
                    hashlib.sha256(args.poses_file.read_bytes()).hexdigest()
                    if args.poses_file
                    else None
                ),
            }
            write_group(out, results, summary)
            print(f"n={n} {members[0]}: {results[0].seconds:.2f} s/view", file=sys.stderr)
            if device == "mps":
                torch.mps.empty_cache()


if __name__ == "__main__":
    main()
