"""Score key-distance error between a tape scene and a reconstruction, in inches."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

METERS_PER_INCH = 0.0254


def _wall_rect(geometry: dict) -> dict | None:
    if geometry.get("kind") == "wall_rect":
        return geometry
    nested = geometry.get("wall_rect")
    return nested if isinstance(nested, dict) else None


def score_kds(gt_scene: dict, recon_scene: dict) -> dict:
    errors: list[dict] = []
    gt_walls = {wall["id"]: wall for wall in gt_scene.get("walls", [])}
    recon_walls = {wall["id"]: wall for wall in recon_scene.get("walls", [])}
    for wall_id, wall in gt_walls.items():
        other = recon_walls.get(wall_id)
        if other is None:
            continue
        delta_m = abs(_baseline_length(wall["baseline_m"]) - _baseline_length(other["baseline_m"]))
        errors.append({"id": f"wall.length[{wall_id}]", "error_in": _inches(delta_m)})
    gt_objects = {item["id"]: item for item in gt_scene.get("objects", [])}
    recon_objects = {item["id"]: item for item in recon_scene.get("objects", [])}
    for object_id, item in gt_objects.items():
        other = recon_objects.get(object_id)
        if other is None:
            continue
        gt_rect = _wall_rect(item.get("geometry") or {})
        recon_rect = _wall_rect(other.get("geometry") or {})
        if not gt_rect or not recon_rect:
            continue
        for index, side in enumerate(("left", "right")):
            delta_m = abs(float(gt_rect["s_m"][index]) - float(recon_rect["s_m"][index]))
            errors.append({"id": f"opening.edge_s[{object_id}:{side}]", "error_in": _inches(delta_m)})
        for key, label in (("bottom_m", "bottom"), ("top_m", "top")):
            if key in gt_rect and key in recon_rect:
                delta_m = abs(float(gt_rect[key]) - float(recon_rect[key]))
                errors.append({"id": f"opening.{label}[{object_id}]", "error_in": _inches(delta_m)})
    inches = [row["error_in"] for row in errors]
    return {
        "errors": errors,
        "n": len(inches),
        "median_in": _percentile(inches, 0.50),
        "p90_in": _percentile(inches, 0.90),
        "max_in": max(inches) if inches else None,
        "share_le_2in": _share(inches, 2.0),
        "share_le_4in": _share(inches, 4.0),
        "share_le_6in": _share(inches, 6.0),
    }


def _inches(delta_m: float) -> float:
    return round(delta_m / METERS_PER_INCH, 6)


def _baseline_length(baseline: list) -> float:
    start, end = baseline
    return ((float(end[0]) - float(start[0])) ** 2 + (float(end[1]) - float(start[1])) ** 2) ** 0.5


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * fraction
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    weight = rank - low
    return ordered[low] * (1.0 - weight) + ordered[high] * weight


def _share(values: list[float], limit: float) -> float | None:
    if not values:
        return None
    return sum(1 for value in values if value <= limit) / len(values)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score tape key distances")
    parser.add_argument("--gt", required=True, type=Path)
    parser.add_argument("--recon", required=True, type=Path)
    args = parser.parse_args(argv)
    for path in (args.gt, args.recon):
        if not path.is_file():
            print(f"missing input: {path}", file=sys.stderr)
            return 3
    report = score_kds(json.loads(args.gt.read_text()), json.loads(args.recon.read_text()))
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
