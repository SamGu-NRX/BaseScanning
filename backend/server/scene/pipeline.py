"""Run dense, then coverage, then scene. ``python -m server.scene.pipeline``."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from server.coverage.run import run_coverage
from server.dense.params import dense_params_from_env
from server.dense.run import load_scale, run_dense
from server.scene.params import scene_params_from_env
from server.scene.run import build_scene


def main() -> None:
    parser = argparse.ArgumentParser(description="WS-06 dense, coverage and scene stages")
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--poses", type=Path, required=True)
    parser.add_argument("--scale", type=Path, default=None)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--objects", type=Path, default=None)
    parser.add_argument("--meter-frames", default="")
    parser.add_argument("--anchor-from-tap", default="")
    parser.add_argument("--cpu", action="store_true")
    parser.add_argument("--max-frames", type=int, default=0)
    args = parser.parse_args()
    packet = json.loads(args.packet.read_text())
    flow = ((packet.get("ext") or {}).get("hss") or {}).get("derived", {}).get("flow", "guided")
    scale, warnings = load_scale(args.scale, flow)
    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    models: dict = {
        "flow": flow,
        "warnings": warnings,
        "source_frame": "colmap_model" if flow == "photo" else "arkit_world",
    }
    if args.max_frames:
        models["max_frames"] = args.max_frames
    del args.cpu
    run_dense(packet, args.poses, scale, out / "dense", params=dense_params_from_env(), models=models)
    override = _parse_meter(args.meter_frames)
    run_coverage(
        packet,
        args.poses,
        out / "dense",
        scale,
        out / "coverage",
        params=scene_params_from_env(),
        meter_override=override,
        anchor_label=args.anchor_from_tap,
        models=models,
    )
    build_scene(
        packet,
        out / "coverage",
        out / "dense",
        args.poses,
        scale,
        args.objects,
        out / "scene",
        params=scene_params_from_env(),
    )
    print(out / "scene" / "scene.json")


def _parse_meter(text: str) -> list[tuple[str, float, float]] | None:
    """``id:u,v`` pairs. Commas separate both the pixel and the next frame."""
    if not text:
        return None
    rows = [(fid, float(u), float(v)) for fid, u, v in re.findall(r"([^:,\s]+):([^,]+),([^,\s]+)", text)]
    if not rows:
        raise ValueError(f"meter frames must look like k00012:1010,655, not {text!r}")
    return rows


if __name__ == "__main__":
    main()
